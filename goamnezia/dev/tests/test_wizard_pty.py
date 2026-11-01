# -*- coding: utf-8 -*-
"""Сквозной прогон мастера первой установки и списка пользователей.

Экраны SSH-меню нельзя проверить ни `bash -n`, ни мостом `--action=`: они живут
только в терминале. Поэтому здесь настоящий install.sh запускается в
псевдотерминале (pty), в него «нажимаются» клавиши, а потом сверяются и экраны,
и то, что легло в config.json / users.json.

Проверяются замечания владельца с первой живой установки (0.2.0):
  • экран режима — «порты свободны» вместо «доступен» и легенда «как выбирать»;
  • профили: порядок 3.1 → 2.0 → 1.x, по умолчанию отмечены 3.1 и 2.0, у
    каждого строка «кому нужен»;
  • галочки: Enter продолжает с отмеченным, ноль — отмена, легенда на экране;
  • порт профиля из низкого диапазона 1024–9999;
  • про DNS больше не спрашивают — молча Google;
  • веб-панель в Lite не предлагается, вместо вопроса одна строка;
  • список пользователей нумерованный, карточка открывается номером;
  • добавление пользователя: без «отображаемого имени», профили галочками,
    Enter = «без лимита» и «бессрочно»;
  • порт доступа агентов: негодный ответ переспрашивается, а предложенный порт
    тоже из низкого диапазона (DEFECTS §1.5).

Docker не нужен: GOAMNEZIA_DRY_RUN=1. Ничего вне временного каталога не пишется.

Запуск: python3 -m unittest tests.test_wizard_pty
"""
import fcntl
import json
import os
import pathlib
import pty
import random
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASH = os.environ.get('BASH5') or shutil.which('bash') or 'bash'
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r')
# Где install.sh ищет команды: он дописывает к PATH системные каталоги.
SYSTEM_PATH = os.pathsep.join([os.environ.get('PATH', ''), '/usr/local/sbin', '/usr/local/bin',
                               '/usr/sbin', '/usr/bin', '/sbin', '/bin'])


def setUpModule():
    # Без jq продукт не пишет ни config.json, ни users.json (_ga_write_json) — мастер «проходит», а
    # состояния нет, и краснел бы весь модуль. Нет инструмента — пропуск с причиной, как в run_static.sh.
    if not shutil.which('jq', path=SYSTEM_PATH):
        raise unittest.SkipTest('нет jq — продукт без него не пишет config.json (apt install jq)')


def require_exec(path):
    """Подставная команда во временном каталоге годится, только если её можно запустить. Где каталог
    временных файлов смонтирован с noexec, продукт её не увидит и пойдёт в настоящую (curl — в
    api.telegram.org, dig — в настоящий DNS): тогда пропуск с причиной, а не красная проверка."""
    if not os.access(str(path), os.X_OK):
        raise unittest.SkipTest('каталог временных файлов смонтирован с noexec — подставную %s не запустить '
                                '(задайте TMPDIR на разделе без noexec)' % os.path.basename(str(path)))


def _session_with_tty():
    """Новая сессия, а pty — её управляющий терминал, как у настоящего SSH-входа.

    Одного setsid мало: на Linux лидер сессии получает управляющий терминал только через TIOCSCTTY (или
    open без O_NOCTTY), и /dev/tty у мастера давал бы ENXIO — вопросы, читающие /dev/tty (confirm_phrase,
    токен GitHub), на Linux ломались бы, а на macOS, где bash берёт терминал сам, — нет. Отказ ioctl не
    роковой: там, где терминал уже взят, повторять нечего."""
    os.setsid()
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass


def clean(text):
    return ANSI.sub('', text)


def user_items(stand):
    """Номера пунктов экрана пользователей: они идут сразу за последней строкой.

    Управление в меню только цифрами, и чтобы номер строки по-прежнему открывал
    карточку, пункты нумеруются после списка. В проверках их считаем так же, как
    считает экран, а не прибиваем числом: иначе соседняя проверка, заведя ещё
    одного человека, ломает эту.
    """
    n = len(stand.users().get('users', []))
    # добавить, найти по имени, раздачи; четвёртым — «Добавить сразу несколько» (стоит вторым на экране), пятым —
    # «Удалить сразу несколько» (третьим на экране)
    return n + 1, n + 4, n + 5, n + 2, n + 3


class Stand:
    """Временный сервер: свой GOAMNEZIA_DIR, свои юниты, никакого /opt."""

    def __init__(self, lang='ru'):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-wiz-'))
        self.dir = self.tmp / 'goamnezia'
        self.dir.mkdir(parents=True)
        (self.dir / '.language').write_text(lang, encoding='utf-8')
        upstream = self.tmp / 'upstream'
        shutil.copytree(ROOT / 'lib' / 'upstream', upstream)
        self.env = dict(os.environ)
        self.env.update({
            'GOAMNEZIA_DRY_RUN': '1',
            'GOAMNEZIA_DIR': str(self.dir),
            'GOAMNEZIA_LOCK_DIR': str(self.tmp / 'run'),
            'GOAMNEZIA_LOG_FILE': str(self.tmp / 'goamnezia.log'),
            'GOAMNEZIA_AUDIT_LOG': str(self.tmp / 'audit.log'),
            'GOAMNEZIA_NOTIFY_DIR': str(self.tmp / 'notify'),
            'GOAMNEZIA_BUILD_ROOT': str(self.tmp / 'build'),
            'GOAMNEZIA_SYSTEMD_DIR': str(self.tmp / 'systemd'),
            'GOAMNEZIA_SERVER_IP': '203.0.113.10',
            'UPSTREAM_DIR': str(upstream),
            'BOT_DIR': str(self.tmp / 'bot'),
            'ADMIN_DIR': str(self.tmp / 'admin'),
            'MCP_DIR': str(self.tmp / 'mcp'),
            'TERM': 'dumb', 'COLUMNS': '120', 'LINES': '60',
            # Внешние адреса машины на стенде — подставные (ga_edge_list, lib/edgeips.sh), по умолчанию
            # их нет, как на macOS без `ip`. Без переменной продукт спросил бы настоящий `ip`, и на
            # Linux-сервере исход решала бы машина: адрес SSH-подключения, если он есть на интерфейсе,
            # мастер берёт вместо GOAMNEZIA_SERVER_IP (_ssh_inbound_ip), и «как обычно: 203.0.113.10»
            # у ключа на другой адрес становилось адресом сервера, где идёт прогон. Проверки со своими
            # адресами задают переменную сами.
            'GOAMNEZIA_EDGE_IPS_STUB': '',
            # Временные файлы с ключами — в каталоге стенда. Без этого на Linux они шли бы в /dev/shm
            # машины (ga_secret_tmpdir), а оборванный kill -9 сеанс оставлял бы их там после close().
            'GOAMNEZIA_SECRET_TMPDIR': str(self.tmp / 'shm'),
        })
        (self.tmp / 'shm').mkdir(mode=0o700)
        # Прогон по SSH не должен становиться частью стенда (см. выше); переменных SSH у мастера нет.
        for var in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY'):
            self.env.pop(var, None)

    def close(self):
        # Временные ссылки на файл (lib/share.sh) живут своим процессом в своей сессии и переживают мастер —
        # гасим их сами, чтобы после прогона на машине не оставалось слушателей до конца их срока.
        for pid in (self.tmp / 'run' / 'share').glob('*/pid'):
            try:
                os.kill(int(pid.read_text().strip()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── прогон ───────────────────────────────────────────────────────────────
    def keys(self, keys, seconds=120):
        """Запустить install.sh в pty и «нажать» keys по мере пауз вывода.

        Очередная клавиша уходит, когда экран замолчал: так мы не обгоняем
        спиннеры и не отвечаем на ещё не заданный вопрос. Когда клавиши
        кончились, ждём ещё немного и выходим — программа может остаться в меню
        на `read -t 30`, и караулить её до общего таймаута незачем.
        """
        master, slave = pty.openpty()
        proc = subprocess.Popen([BASH, str(ROOT / 'install.sh')],
                                stdin=slave, stdout=slave, stderr=slave,
                                env=self.env, cwd=str(ROOT), close_fds=True,
                                preexec_fn=_session_with_tty)
        os.close(slave)
        out = b''
        queue = list(keys)
        idle = 0
        quiet = 0
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.4)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                idle = 0
                quiet = 0
                continue
            if queue:
                # ДВА подряд тихих такта, а не один: отрисовка экрана под
                # нагрузкой прерывается дольше четырёхсот миллисекунд, и
                # клавиша уходила на недорисованный экран — мастер отменялся,
                # а тест падал на экране, до которого не дошёл. Цена — лишние
                # 0,4 с на клавишу, польза — набор перестаёт мигать.
                quiet += 1
                if quiet < 2:
                    continue
                item = queue.pop(0)
                if isinstance(item, tuple) and item[0] == 'until':
                    # («until», текст) — дождаться текста на экране, ничего не нажимая. Тишина вывода под нагрузкой
                    # полного прогона бывает и посреди шага (ключ, QR, jq), и клавиша или смена окна уходили раньше
                    # экрана, для которого они (TAIL-23, TAIL-25). Текста ещё нет — ждём дальше, до общего срока.
                    if item[1] not in clean(out.decode('utf-8', 'replace')):
                        queue.insert(0, item)
                        continue
                elif isinstance(item, tuple) and item[0] == 'sleep':
                    # («sleep», секунды) — человек отошёл: ничего не нажимаем, время идёт (срок показа адреса панели)
                    time.sleep(item[1])
                elif isinstance(item, tuple) and item[0] == 'winsize':
                    # («winsize», строк, колонок) — человек развернул окно: размер pty меняется, а клавиши нет.
                    # Продукт перечитывает размер с терминала (stty size) перед тем, как рисовать QR.
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', item[1], item[2], 0, 0))
                else:
                    os.write(master, item.encode())
                quiet = 0
                idle = 0
                continue
            if proc.poll() is not None:
                break
            idle += 1
            if idle >= 4:          # ~1.6 с тишины после последней клавиши
                break
        # Группа процессов — та, что создал setsid: её номер равен номеру лидера. getpgid() здесь не
        # годится: если poll() уже собрал лидера, он падает, и оставшиеся в группе процессы жили дальше.
        try:
            os.killpg(proc.pid, 9)
        except Exception:
            pass
        os.close(master)
        return clean(out.decode('utf-8', 'replace'))

    def pipe_answers(self, answers, seconds=120):
        """Ответы из пайпа, а потом КОНЕЦ ВВОДА — оборванная сессия.

        Этим проверяются вопросы, у которых на EOF не было выхода: read отдаёт
        конец ввода мгновенно, и цикл `while true` крутится вхолостую на 100 %
        CPU. Терминала здесь нет намеренно: pty так не оборвать.
        """
        proc = subprocess.Popen([BASH, str(ROOT / 'install.sh')],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=self.env, cwd=str(ROOT), close_fds=True,
                                preexec_fn=os.setsid)
        try:
            out = proc.communicate(input=''.join(answers).encode(), timeout=seconds)[0]
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), 9)
            raise AssertionError('вопрос завис на конце ввода — busy-loop на read')
        return clean(out.decode('utf-8', 'replace'))

    def pipe(self, seconds=120):
        """Запуск без терминала вовсе — как `curl … | bash` в автоматизации."""
        proc = subprocess.Popen([BASH, str(ROOT / 'install.sh')],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=self.env, cwd=str(ROOT), close_fds=True,
                                preexec_fn=os.setsid)
        try:
            out = proc.communicate(timeout=seconds)[0]
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), 9)
            raise AssertionError('мастер завис без терминала — busy-loop на read')
        return clean(out.decode('utf-8', 'replace'))

    # ── состояние ────────────────────────────────────────────────────────────
    def config(self):
        p = self.dir / 'config.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

    def users(self):
        p = self.dir / 'users.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


# Клавиши мастера «всё по умолчанию»: 4 — Lite (0.6.0: 1 Ленивый Pro, 2 Pro, 3 Semi-Pro, 4 Lite), Enter —
# профили как есть, Enter — предложенный порт, n — без бота, Enter — пауза, 0 — выход из меню.
LITE_DEFAULTS = ['4\n', '\n', '\n', 'n\n', '\n', '0\n']


class WizardLite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.out = cls.stand.keys(LITE_DEFAULTS)
        cls.cfg = cls.stand.config()

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_wizard_finished(self):
        self.assertTrue(self.cfg.get('installed'), self.out[-3000:])
        self.assertEqual(self.cfg.get('mode'), 'lite')

    # ── замечание 3: экран режима ────────────────────────────────────────────
    def test_mode_screen_talks_about_ports_and_has_a_legend(self):
        self.assertIn('порты свободны', self.out)
        self.assertNotRegex(self.out, r'\)\s+Lite\s.*✓ доступен')
        # Строка легенды переехала в preflight_legend_marks: «Значки: …».
        # Перенос строк в узком терминале рвёт фразу, поэтому ищем по кускам.
        self.assertIn('✓ — режим можно выбрать', self.out)
        self.assertIn('Как выбрать: 1 —', self.out)

    # ── замечание 4: порядок и умолчания профилей ────────────────────────────
    def test_profiles_order_defaults_and_notes(self):
        screen = self.out.split('Какие профили поставить?')[1].split('Ваш выбор')[0]
        rows = [l.strip() for l in screen.splitlines() if ') Amnezia' in l]
        self.assertEqual(len(rows), 3, screen)
        self.assertIn('3.1', rows[0])
        self.assertIn('2.0', rows[1])
        self.assertIn('AmneziaWG 1.5', rows[2])     # свободный awg1 — линией, которая встанет (LABEL-02)
        self.assertTrue(rows[0].startswith('[x]'), rows[0])
        self.assertTrue(rows[1].startswith('[x]'), rows[1])
        self.assertTrue(rows[2].startswith('[ ]'), rows[2])
        # Подсказки переносятся по словам — ищем по тексту без переносов.
        flat = ' '.join(screen.split())
        for note in ('свежие версии приложений Amnezia', 'прошлые версии приложений Amnezia',
                     'роутеры Keenetic'):
            self.assertIn(note, flat)
        # B-4 (владелец 16.10): Keenetic на обычной прошивке понимает 1.5 и 2.0 — подсказка у 2.0 называет
        # роутеры Keenetic; 3.1 — пока только в dev-прошивке, у 1.5 это сказано.
        row20 = ' '.join(screen.split('2) AmneziaWG 2.0', 1)[1].split('3) AmneziaWG', 1)[0].split())
        self.assertIn('роутеры Keenetic', row20)
        self.assertIn('3.1 — пока только в dev-прошивке', flat)

    # ── NC-18: номер версии зависит от платформы — одного числа в SSH больше нет ──
    def test_no_single_app_version_number_next_to_the_key_or_the_profiles(self):
        profiles = self.out.split('Какие профили поставить?')[1].split('Ваш выбор')[0]
        self.assertNotRegex(profiles, r'\d+\.\d+\.\d+', profiles)
        key = ' '.join(self.out.split('Готово. Ключ администратора ниже.', 1)[1].split())
        self.assertNotIn('4.8.12.7', key)
        self.assertNotIn('5.0.1.5', key)
        # «Только VPN»: страницы подключения нет — и отсылки к ней нет (NC-26)
        self.assertIn('С какой версии оно читает этот ключ, зависит от устройства — просто обновите приложение до '
                      'последней.', key)
        self.assertNotIn('на странице подключения', key)

    def test_default_profiles_are_installed(self):
        self.assertEqual(sorted(self.cfg.get('profiles', {})), ['awg2', 'awg3'])

    # ── замечание 5: легенда галочек и Enter ─────────────────────────────────
    def test_checkbox_legend_says_enter_continues(self):
        self.assertIn('Цифра — переключить галочку, Enter — продолжить (не знаете — просто Enter), 0 — назад.',
                      self.out)
        self.assertIn('Ваш выбор (Enter — продолжить)', self.out)
        # Прежнего «0) Продолжить» на экране больше нет — ноль это отмена.
        self.assertNotIn('0) Продолжить', self.out)

    # ── замечание 6: порт ниже десяти тысяч ──────────────────────────────────
    def test_ports_are_below_ten_thousand(self):
        for pid, prof in self.cfg.get('profiles', {}).items():
            with self.subTest(profile=pid):
                self.assertGreaterEqual(prof['port'], 1024)
                self.assertLessEqual(prof['port'], 9999)

    def test_suggested_port_in_the_prompt_is_low_too(self):
        # Экран портов (0.4.3): у каждого выбранного профиля своя строка с портом.
        m = re.search(r'\n    AmneziaWG 2\.0\s+(\d+)\n', self.out)
        self.assertIsNotNone(m, self.out[:4000])
        self.assertTrue(1024 <= int(m.group(1)) <= 9999, m.group(1))

    def test_ports_screen_lists_every_profile_and_ports_differ(self):
        self.assertIn('Порты профилей (UDP)', self.out)
        self.assertIn('Ваш выбор (Enter — продолжить установку, 1 — свои порты, 0 — назад)', self.out)
        self.assertIn('менять ничего не нужно', ' '.join(self.out.split()))
        # Строка экрана — линия, которая встанет (без «— не установлена» и без порта: он — колонкой), и порт
        screen = self.out.split('Порты профилей (UDP)', 1)[1].split('Ваш выбор', 1)[0]
        shown = dict(re.findall(r'^    AmneziaWG (3\.1|2\.0|1\.5)\s+(\d+)$', screen, re.M))
        self.assertEqual(sorted(shown), ['2.0', '3.1'], self.out[-3000:])
        ports = {prof['line']: prof['port'] for prof in self.cfg.get('profiles', {}).values()}
        # Ставятся ровно те порты, что показаны на экране, и они разные
        self.assertEqual({k: int(v) for k, v in shown.items()}, ports)
        self.assertEqual(len(set(ports.values())), len(ports))

    def test_profile_notes_keep_their_indent_when_wrapped(self):
        # Хвост пояснения («маскировка», «приложение») раньше падал на край окна
        screen = self.out.split('Какие профили поставить?')[1].split('Ваш выбор')[0]
        for line in screen.splitlines():
            if line.strip() and not line.lstrip().startswith(('[', 'Цифра')):
                self.assertTrue(line.startswith('         '), repr(line))

    # ── замечание 7: про DNS не спрашивают ───────────────────────────────────
    def test_no_dns_question_google_by_default(self):
        self.assertNotIn('DNS для клиентских конфигов', self.out)
        self.assertNotIn('AdGuard — 94.140.14.14', self.out)
        self.assertEqual(self.cfg.get('client_dns'), ['8.8.8.8', '8.8.4.4'])
        self.assertIn('DNS клиентов: 8.8.8.8 и 8.8.4.4 (Google)', self.out)

    # ── замечание 8: где взять токен ─────────────────────────────────────────
    def test_bot_offer_still_asked(self):
        self.assertIn('Установить Telegram-бот сейчас?', self.out)

    def test_bot_offer_asks_only_for_the_token(self):
        # NC-04: в 0.5.0 Telegram ID не нужен — администратор привязывается сообщением боту
        q = ' '.join(self.out.split('Установить Telegram-бот сейчас?', 1)[1].split('[1 — да', 1)[0].split())
        self.assertIn('только токен от @BotFather', q)
        self.assertNotIn('Telegram ID', q)

    # ── замечание 10: панель без домена не предлагается — с 0.5.0 (NC-13) она ставится молча ──
    def test_panel_is_not_offered_without_a_domain(self):
        self.assertNotIn('Установить веб-панель?', self.out)
        # Вопроса нет и теперь: панель ставится сама, для SSH-туннеля (подробно — PanelInEveryMode)
        self.assertIn('DRY: admin-web/install_admin.sh --auto', self.out)

    def test_first_user_created(self):
        names = [u['name'] for u in self.stand.users().get('users', [])]
        self.assertEqual(names, ['admin'])

    # ── NC-28: первый экран без команды docker ──
    def test_container_search_line_has_no_command(self):
        self.assertIn('Ищу на сервере контейнеры Amnezia, и остановленные тоже…', self.out, self.out[:3000])
        self.assertNotIn('docker ps', self.out)

    def test_no_takeover_screen_on_a_clean_server(self):
        # Контейнеров Amnezia нет — первый экран мастера прежний, без таблицы «что нашли» (OWN-1).
        self.assertNotIn('На сервере уже есть контейнеры Amnezia', self.out)


class WizardPorts(unittest.TestCase):
    """Порты всех выбранных профилей на одном экране (0.4.3).

    Раньше мастер спрашивал порт только у awg2: «0 — назад» ставил его на другой
    случайный порт, а случайный порт awg3 мог совпасть с портом awg2 — и мастер
    обрывался на полпути.
    """

    def run_keys(self, keys, stand=None):
        own = stand is None
        stand = stand or Stand()
        try:
            out = stand.keys(keys)
            return out, stand.config()
        finally:
            if own:
                stand.close()

    def test_own_ports_for_three_profiles(self):
        # 4 — Lite, 3 — отметить awg1, Enter; 1 — задать свои: 5001, Enter (awg2 — предложенный), 5003; Enter
        out, cfg = self.run_keys(['4\n', '3\n', '\n', '1\n', '5001\n', '\n', '5003\n', '\n', 'n\n', '\n', '0\n'])
        ports = {k: v['port'] for k, v in cfg.get('profiles', {}).items()}
        self.assertEqual(ports.get('awg3'), 5001, out[-3000:])
        self.assertEqual(ports.get('awg1'), 5003, out[-3000:])
        self.assertIn('awg2', ports)
        self.assertEqual(len(set(ports.values())), 3)

    def test_duplicate_is_refused_and_zero_steps_back(self):
        # 5001 для awg3, 5001 для awg2 — отказ; 0 — назад к awg3; 5002; 5001 для awg2; Enter
        out, cfg = self.run_keys(['4\n', '\n', '1\n', '5001\n', '5001\n', '0\n', '5002\n', '5001\n', '\n',
                                  'n\n', '\n', '0\n'])
        # Профили ставит сам мастер — линией, которая встанет, без «— не установлена» (DUP-LINE-01)
        self.assertIn('Порт 5001 уже выбран для AmneziaWG 3.1.', out)
        self.assertIn('UDP-порт для AmneziaWG 3.1 (Enter — 5001, 0 — назад)', out)
        ports = {k: v['port'] for k, v in cfg.get('profiles', {}).items()}
        self.assertEqual(ports, {'awg3': 5002, 'awg2': 5001}, out[-3000:])

    def test_zero_on_ports_screen_goes_back_to_profiles_and_keeps_ports(self):
        out, cfg = self.run_keys(['4\n', '\n', '0\n', '\n', '\n', 'n\n', '\n', '0\n'])
        screens = re.findall(r'\n    AmneziaWG 2\.0\s+(\d+)\n', out)
        self.assertGreaterEqual(len(screens), 2, out[-3000:])
        # Шаг назад не перетасовывает предложенные порты
        self.assertEqual(screens[0], screens[1])
        self.assertEqual(out.count('Какие профили поставить?'), 2)
        self.assertTrue(cfg.get('installed'), out[-3000:])
        self.assertNotIn('Отменено', out.split('Порты профилей')[1].split('DNS клиентов')[0])

    def test_site_mode_443_offers_the_third_mode_and_no_keeps_it(self):
        # 3 — Semi-Pro, домен пусто; 1; Enter (awg3); 443 для awg2 → «переключиться?» 0 — нет;
        # Enter (предложенный); Enter — принять; 0 — шаблон; n — бот
        out, cfg = self.run_keys(['3\n', '\n', '\n', '1\n', '\n', '443\n', '0\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        self.assertIn('UDP/443 для VPN — это режим Pro.', ' '.join(out.split()))
        self.assertIn('Переключиться на режим Pro?', out)
        self.assertNotIn(443, [v['port'] for v in cfg.get('profiles', {}).values()])
        self.assertEqual(cfg.get('mode'), 'lite_site')

    def test_site_mode_443_switches_to_the_third_mode_on_yes(self):
        out, cfg = self.run_keys(['3\n', '\n', '\n', '1\n', '\n', '443\n', '1\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        self.assertEqual(cfg.get('mode'), 'pro', out[-3000:])
        self.assertEqual(cfg['profiles']['awg2']['port'], 443)

    def test_leading_zero_is_the_same_port(self):
        # 2 — Pro; домен пусто; Enter — линия 2.0 (LZ-11); 1; «0443» для awg3 — это 443; awg2 предложен уже не 443
        out, cfg = self.run_keys(['2\n', '\n', '\n', '\n', '1\n', '0443\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        ports = {k: v['port'] for k, v in cfg.get('profiles', {}).items()}
        self.assertEqual(ports.get('awg3'), 443, out[-3000:])
        self.assertNotEqual(ports.get('awg2'), 443)

    def test_zero_on_domain_goes_back_to_the_mode_screen(self):
        # 3 — Semi-Pro; 0 на домене — назад; 4 — Lite; дальше по умолчанию
        out, cfg = self.run_keys(['3\n', '0\n', '4\n', '\n', '\n', 'n\n', '\n', '0\n'])
        self.assertGreaterEqual(out.count('Как выбрать: 1 —'), 2, out[-3000:])
        self.assertNotIn('Домен не похож на домен', out)
        self.assertEqual(cfg.get('mode'), 'lite', out[-3000:])
        self.assertTrue(cfg.get('installed'))

    def test_domain_is_kept_when_stepping_back_to_it(self):
        # 3 — Semi-Pro; example.com; 0 на галочках — назад к домену; Enter — оставить example.com; дальше
        out, cfg = self.run_keys(['3\n', 'example.com\n', '0\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        self.assertIn('Ваш домен (Enter — оставить example.com, 0 — назад)', out, out[-3000:])
        self.assertEqual(cfg.get('domain'), 'example.com', out[-3000:])

    def test_mode_change_after_stepping_back_gives_443_to_awg2(self):
        # 4 — Lite; Enter; 0 на портах; 0 на галочках; 2 — Pro; Enter (домен пусто); Enter (линия 2.0); Enter; Enter
        out, cfg = self.run_keys(['4\n', '\n', '0\n', '0\n', '2\n', '\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        self.assertEqual(cfg.get('mode'), 'pro', out[-3000:])
        self.assertEqual(cfg['profiles']['awg2']['port'], 443, out[-3000:])

    def test_pro_puts_awg2_on_443_by_default(self):
        out, cfg = self.run_keys(['2\n', '\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        self.assertEqual(cfg['profiles']['awg2']['port'], 443, out[-3000:])
        self.assertIn('на том же порту, что и сайт', out)
        self.assertEqual(cfg.get('mode'), 'pro')

    def test_pro_443_can_move_to_another_profile_but_only_one_holds_it(self):
        # 2 — Pro; Enter — линия 2.0; 1; 443 для awg3; Enter для awg2 (ему предложен уже не 443); Enter
        out, cfg = self.run_keys(['2\n', '\n', '\n', '\n', '1\n', '443\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
        ports = {k: v['port'] for k, v in cfg.get('profiles', {}).items()}
        self.assertEqual(ports.get('awg3'), 443, out[-3000:])
        self.assertNotEqual(ports.get('awg2'), 443)
        self.assertRegex(out, r'UDP-порт для AmneziaWG 2\.0 \(Enter — \d+, 0 — назад\)')
        self.assertNotRegex(out, r'UDP-порт для AmneziaWG 2\.0 \(Enter — 443,')
        self.assertEqual(cfg.get('mode'), 'pro')

    def test_pro_without_443_offers_the_site_mode(self):
        # 2 — Pro; Enter — линия 2.0; 1; Enter (awg3); 5005 для awg2; Enter — принять; 1 — да, режим Semi-Pro
        out, cfg = self.run_keys(['2\n', '\n', '\n', '\n', '1\n', '\n', '5005\n', '\n', '1\n', '0\n', 'n\n', '\n', '0\n'])
        self.assertIn('Ни один профиль не стоит на UDP/443', out)
        self.assertEqual(cfg.get('mode'), 'lite_site', out[-3000:])
        self.assertEqual(cfg['profiles']['awg2']['port'], 5005)


class WizardChoices(unittest.TestCase):
    """Галочки действительно переключаются, а ноль отменяет."""

    def test_digit_adds_the_third_profile(self):
        stand = Stand()
        try:
            # 4 — Lite, «3» отмечает 1.0/1.5, Enter — продолжить.
            stand.keys(['4\n', '3\n', '\n', '\n', 'n\n', '\n', '0\n'])
            self.assertEqual(sorted(stand.config().get('profiles', {})),
                             ['awg1', 'awg2', 'awg3'])
        finally:
            stand.close()

    def test_digit_unticks_the_default(self):
        stand = Stand()
        try:
            # «1» снимает галочку с 3.1 — остаётся только 2.0.
            stand.keys(['4\n', '1\n', '\n', '\n', 'n\n', '\n', '0\n'])
            self.assertEqual(sorted(stand.config().get('profiles', {})), ['awg2'])
        finally:
            stand.close()

    def test_awg2_is_locked(self):
        """2.0 снять нельзя: только его видит официальное приложение Amnezia."""
        stand = Stand()
        try:
            out = stand.keys(['4\n', '2\n', '\n', '\n', 'n\n', '\n', '0\n'])
            self.assertIn('AmneziaWG 2.0 ставится всегда', out)
            self.assertIn('awg2', stand.config().get('profiles', {}))
        finally:
            stand.close()

    def test_zero_steps_back_and_zero_on_the_first_screen_leaves(self):
        # 0.4.3: ноль на галочках — шаг назад к режиму, а не обрыв мастера;
        # выйти совсем — ноль на первом экране, и мастер говорит, что будет дальше.
        stand = Stand()
        try:
            out = stand.keys(['4\n', '0\n', '0\n'])
            self.assertEqual(out.count('Какие профили поставить?'), 1, out[-3000:])
            self.assertGreaterEqual(out.count('Как выбрать: 1 —'), 2, out[-3000:])
            self.assertIn('Ничего не установлено', out)
            self.assertNotEqual(stand.config().get('installed'), True)
        finally:
            stand.close()


class WizardWithoutTerminal(unittest.TestCase):
    """`curl … | bash` без терминала и оборванная сессия: остановка без установки — не умолчания и не зависание.

    INST-02 (сервер 168, 04.10.2026): конец ввода на любом вопросе мастера раньше значил «дальше с умолчаниями», и
    мастер, у которого пропал терминал, сам поставил Docker, awg3 и awg2 — без человека. Ставить без терминала —
    `install.sh --lazy --domain=…` (там конец ввода тоже отмена)."""

    def test_pipe_run_stops_and_installs_nothing(self):
        stand = Stand()
        try:
            out = stand.pipe(seconds=180)
            cfg = stand.config()
        finally:
            stand.close()
        flat = ' '.join(out.split())
        self.assertIn('Ввод оборвался — без вас дальше не иду и ничего не ставлю. Запустите установку снова '
                      '(goamnezia) — продолжу с того же места.', flat, out[-3000:])
        self.assertNotEqual(cfg.get('installed'), True, out[-3000:])
        self.assertEqual(cfg.get('profiles', {}), {})
        self.assertNotIn('Ставлю Docker', out)
        # Вопрос задан один раз — без зацикливания; меню без человека не рисуется: как продолжить, уже сказано
        self.assertEqual(out.count('Выбор (Enter — 4, Lite)'), 1, out[-3000:])
        self.assertNotIn('панель управления', out)


def bridge(stand, *args):
    """Мост стенда (как бот): `install.sh --action=… --json`, ответ — последняя строка stdout."""
    p = subprocess.run([BASH, str(ROOT / 'install.sh')] + list(args) + ['--json'], env=stand.env, cwd=str(ROOT),
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    lines = p.stdout.decode('utf-8', 'replace').strip().splitlines()
    return p.returncode, json.loads(lines[-1]) if lines else {}


# Повторный запуск после обрыва: Enter — продолжить; дальше ждём вопрос о шаблоне сайта (сайт поднят), 0 — оставить
# случайный; n — без бота; Enter — в меню из «Что дальше»; 0 — выход.
RESUME_TAIL = [('until', 'Выбрать другой из каталога'), '0\n', 'n\n', '\n', '0\n']


class WizardInterruptedResumes(unittest.TestCase):
    """INST-01, INST-02 (сервер 168, 04.10.2026). Терминал пропал на вопросе «Какая линия AmneziaWG встанет на UDP
    443», и мастер без человека поставил профили по умолчанию; повторный запуск спрашивал всё заново, споткнулся о
    «Профиль awg2 уже установлен», сайт Pro не поднял, а меню показывало «Режим: Pro».

    Теперь конец ввода — остановка с запомненными ответами (запись .wizard в config.json), шапка меню говорит, что
    установка не закончена, а повторный запуск продолжает с того же вопроса и доводит установку до конца. Режим с
    сайтом в config.json появляется, только когда сайт поднят."""

    @classmethod
    def setUpClass(cls):
        cls.stand = lazy_stand()
        # 2 — Pro, домен — и ввод кончился на вопросе о линии на UDP 443
        cls.out_eof = cls.stand.pipe_answers(['2\n', 'example.com\n'], seconds=180)
        cls.cfg_eof = cls.stand.config()
        # Шапка меню, пока установка не закончена: «0 — в меню» на экране продолжения, 0 — выход
        cls.out_menu = cls.stand.keys(['0\n', '0\n'])
        # Повторный запуск: Enter — продолжить; Enter — 2.0 на 443; Enter — профили; Enter — порты; дальше — установка
        cls.out = cls.stand.keys(['\n', '\n', '\n', '\n'] + RESUME_TAIL, seconds=300)
        cls.cfg = cls.stand.config()
        cls.users = [u['name'] for u in cls.stand.users().get('users', [])]

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_end_of_input_installs_nothing_and_remembers_the_answers(self):
        flat = ' '.join(self.out_eof.split())
        self.assertIn('Какая линия AmneziaWG встанет на UDP 443', flat, self.out_eof[-3000:])
        self.assertIn('Ввод оборвался — без вас дальше не иду и ничего не ставлю', flat)
        self.assertNotIn('Ставлю Docker', self.out_eof)
        self.assertNotIn('Какие профили поставить?', self.out_eof)
        cfg = self.cfg_eof
        self.assertEqual(cfg.get('profiles', {}), {}, self.out_eof[-3000:])
        self.assertNotEqual(cfg.get('installed'), True)
        # Желаемый режим и домен — в записи ответов, а не в mode/domain: сайта ещё нет, и режим честный — Lite
        self.assertEqual(cfg.get('mode', 'lite'), 'lite')
        self.assertFalse(cfg.get('domain'))
        wz = cfg.get('wizard', {})
        self.assertEqual((wz.get('step'), wz.get('mode'), wz.get('domain')), ('line', 'pro', 'example.com'), cfg)

    def test_menu_shows_the_real_mode_and_says_the_install_is_unfinished(self):
        head = self.out_menu.split('панель управления', 1)[-1].split('1) Пользователи', 1)[0]
        flat = ' '.join(head.split())
        self.assertIn('Режим: Lite', flat, self.out_menu[-3000:])
        self.assertNotIn('Режим: Pro', flat)
        self.assertIn('Установка не закончена (выбран режим «Pro»). Продолжить — выйдите (0) и запустите goamnezia '
                      'снова: мастер продолжит с того же места.', flat)

    def test_second_run_continues_from_the_same_question(self):
        flat = ' '.join(self.out.split())
        self.assertIn('Установка не закончена — продолжу с того же места', flat, self.out[:3000])
        self.assertIn('Режим: Pro Домен: example.com Дальше — какая линия встанет на UDP 443.', flat)
        # Режим и домен не спрашиваются заново; вопрос о линии — тот, на котором оборвалось
        self.assertNotIn('Как выбрать: 1 —', self.out)
        self.assertNotIn('Ваш домен', self.out)
        self.assertIn('Какая линия AmneziaWG встанет на UDP 443', flat)

    def test_the_install_is_finished_with_the_site(self):
        cfg = self.cfg
        self.assertIs(cfg.get('installed'), True, self.out[-3000:])
        self.assertEqual((cfg.get('mode'), cfg.get('domain')), ('pro', 'example.com'))
        self.assertEqual(cfg.get('site', {}).get('cert'), 'letsencrypt')
        self.assertEqual(cfg['profiles']['awg2']['port'], 443)
        self.assertIn('awg3', cfg['profiles'])
        self.assertNotIn('wizard', cfg)
        self.assertTrue((self.stand.tmp / 'nginx' / 'sites-available' / 'goamnezia').exists())
        self.assertEqual(self.users, ['admin'])
        self.assertIn('Готово. Ключ администратора ниже.', self.out)


def interrupted_install_stand(legacy, awg2_port=443, running=('awg3', 'awg2')):
    """Сервер после прерванной установки: профили awg3 (3049) и awg2 стоят, первого пользователя, сайта и панели нет.

    legacy — как оставил мастер до 1.0.10 (сервер 168): желаемый режим Pro и домен уже в config.json (мастер писал их
    до установки), записи ответов нет. Иначе — как оставляет мастер этой версии: режим Lite, ответы в записи .wizard
    (Pro, домен, 2.0 на 443, порты 3049 и 443). running — какие контейнеры «работают» (каталоги-фикстуры)."""
    st = lazy_stand()
    version = re.search(r'^GOAMNEZIA_VERSION="([^"]+)"', (ROOT / 'lib' / 'common.sh').read_text(encoding='utf-8'),
                        re.M).group(1)
    (st.dir / 'config.json').write_text(json.dumps({
        'schema': 1, 'version': version, 'installed': False, 'mode': 'lite', 'lang': 'ru', 'domain': '',
        'server_ip': '203.0.113.10', 'endpoint_host': '', 'client_dns': ['8.8.8.8', '8.8.4.4'], 'profiles': {}}),
        encoding='utf-8')
    os.chmod(st.dir / 'config.json', 0o600)
    cfs = st.tmp / 'cfs'
    cfs.mkdir()
    st.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
    for prof, port in (('awg3', 3049), ('awg2', awg2_port)):
        rc, js = bridge(st, '--action=profile-install', '--profile=' + prof, '--port=%d' % port)
        assert rc == 0 and js.get('status') == 'success', js
    for prof in ('awg3', 'awg2'):
        if prof not in running:
            shutil.rmtree(cfs / ('amnezia-' + prof), ignore_errors=True)
        else:
            (cfs / ('amnezia-' + prof)).mkdir(exist_ok=True)
    cfg = st.config()
    if legacy:
        cfg.update({'mode': 'pro', 'domain': 'example.com'})
    else:
        cfg['wizard'] = {'step': 'install', 'started': '2026-10-04T12:00:00Z', 'mode': 'pro',
                         'domain': 'example.com', 'line443': 'awg2', 'awg3': True, 'awg1': False,
                         'ports': {'awg3': 3049, 'awg2': 443}}
    (st.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
    return st


class WizardOldInterruptedInstall(unittest.TestCase):
    """INST-01: сервер, где установку прежней версии прервали после профилей (как 168): installed=true, в config.json
    «Режим: Pro» и домен, а сайта, сертификата, панели и первого пользователя нет. Прежде меню так и показывало
    «Режим: Pro», а «Сменить режим → Pro» сайт не поднимало (режим и так pro). Теперь запуск goamnezia узнаёт такую
    установку по фактам (vhost нет, вида сертификата нет), честно пишет Lite и предлагает продолжить: стоящие профили
    не переставляются, дальше — первый пользователь, сайт, сертификат и панель."""

    @classmethod
    def setUpClass(cls):
        cls.stand = interrupted_install_stand(legacy=True)
        cls.cfg_before = cls.stand.config()
        cls.out = cls.stand.keys(['\n'] + RESUME_TAIL, seconds=300)
        cls.cfg = cls.stand.config()
        cls.flat = ' '.join(cls.out.split())

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_the_wizard_offers_to_continue_and_names_what_is_installed(self):
        self.assertIn('Установка не закончена — продолжу с того же места', self.flat, self.out[-3000:])
        # Порт стоит в самой фразе или колонкой — подпись без него (решение владельца 15.10, DUP-LINE-01).
        self.assertIn('Режим: Pro Домен: example.com Линия на UDP 443: AmneziaWG 2.0 Профили: '
                      'AmneziaWG 3.1 — порт 3049 · уже стоит AmneziaWG 2.0 — порт 443 · уже стоит',
                      self.flat)

    def test_installed_profiles_are_kept_not_reinstalled(self):
        # Порт стоит в самой фразе или колонкой — подпись без него (решение владельца 15.10, DUP-LINE-01).
        self.assertIn('✓ AmneziaWG 3.1 уже стоит и работает (порт 3049) — оставляю как есть.', self.flat,
                      self.out[-4000:])
        self.assertIn('✓ AmneziaWG 2.0 уже стоит и работает (порт 443) — оставляю как есть.', self.flat)
        self.assertNotIn('Ставлю профиль', self.out)
        self.assertNotIn('уже установлен — сначала удалите', self.flat)
        for p in ('awg3', 'awg2'):
            self.assertEqual(self.cfg['profiles'][p]['server_pubkey'], self.cfg_before['profiles'][p]['server_pubkey'])
            self.assertEqual(self.cfg['profiles'][p]['port'], self.cfg_before['profiles'][p]['port'])

    def test_the_rest_is_installed_site_first_user_and_the_mode_is_real(self):
        cfg = self.cfg
        self.assertEqual((cfg.get('mode'), cfg.get('domain')), ('pro', 'example.com'), self.out[-3000:])
        self.assertEqual(cfg.get('site', {}).get('cert'), 'letsencrypt')
        self.assertTrue((self.stand.tmp / 'nginx' / 'sites-available' / 'goamnezia').exists())
        self.assertNotIn('wizard', cfg)
        self.assertEqual([u['name'] for u in self.stand.users().get('users', [])], ['admin'])
        self.assertIn('Сайт на домене включён: https://example.com/', self.flat)


class WizardResumeKeepsTheWorkingPort(unittest.TestCase):
    """INST-01: порт уже стоящего профиля разошёлся с выбранным в мастере (в записи — 443, а awg2 стоит на 5555:
    переставили в «Протоколах» или успела вторая установка). Мастер не двигает работающий профиль молча: оставляет
    5555 и говорит об этом, а раз на UDP 443 никого нет — спрашивает, поставить ли Semi-Pro. Лежащий контейнер
    своего профиля (awg3) поднимается из копии состояния — ключи прежние, профиль не переставляется."""

    @classmethod
    def setUpClass(cls):
        cls.stand = interrupted_install_stand(legacy=False, awg2_port=5555, running=('awg2',))
        cls.cfg_before = cls.stand.config()
        # Enter — продолжить; Enter — порты как есть; 1 — да, режим Semi-Pro; дальше — установка
        cls.out = cls.stand.keys(['\n', '\n', '1\n'] + RESUME_TAIL, seconds=300)
        cls.cfg = cls.stand.config()
        cls.flat = ' '.join(cls.out.split())

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_the_port_stays_and_it_is_said_aloud(self):
        # Порт стоит в самой фразе или колонкой — подпись без него (решение владельца 15.10, DUP-LINE-01).
        self.assertIn('⚠ AmneziaWG 2.0 уже стоит на порту 5555, а в мастере выбран 443 — оставляю 5555: порт '
                      'работающего профиля мастер не двигает.', self.flat, self.out[-4000:])
        self.assertEqual(self.cfg['profiles']['awg2']['port'], 5555)
        self.assertEqual(self.cfg['profiles']['awg2']['server_pubkey'],
                         self.cfg_before['profiles']['awg2']['server_pubkey'])

    def test_without_a_profile_on_443_the_mode_becomes_semi_pro_by_answer(self):
        self.assertIn('Ни один профиль не стоит на UDP/443', self.flat)
        self.assertEqual(self.cfg.get('mode'), 'lite_site', self.out[-3000:])
        self.assertNotIn('wizard', self.cfg)

    def test_a_stopped_own_profile_is_brought_back_not_reinstalled(self):
        self.assertIn('ℹ AmneziaWG 3.1 уже стоит, но контейнер не работает — поднимаю его из копии состояния',
                      self.flat, self.out[-4000:])
        self.assertNotIn('Ставлю профиль', self.out)
        self.assertEqual(self.cfg['profiles']['awg3']['server_pubkey'],
                         self.cfg_before['profiles']['awg3']['server_pubkey'])


class OneWizardAtATime(unittest.TestCase):
    """INST-01: на сервере 168 шли два мастера сразу — первый, оставшийся без терминала, ставил профили, а второй
    насчитал «awg2 ещё нет» и оборвался на «Профиль awg2 уже установлен». Второй заход, пока первый жив, называет его
    и ничего не трогает; замок убитого мастера не мешает следующему."""

    def test_second_wizard_names_the_first_and_touches_nothing(self):
        st = Stand()
        master, slave = pty.openpty()
        first = subprocess.Popen([BASH, str(ROOT / 'install.sh')], stdin=slave, stdout=slave, stderr=slave,
                                 env=st.env, cwd=str(ROOT), close_fds=True, preexec_fn=_session_with_tty)
        os.close(slave)
        seen = b''
        try:
            deadline = time.time() + 120
            while time.time() < deadline and 'Выбор (Enter — 4, Lite)' not in clean(seen.decode('utf-8', 'replace')):
                ready, _, _ = select.select([master], [], [], 0.5)
                if ready:
                    try:
                        seen += os.read(master, 65536)
                    except OSError:
                        break
            self.assertIn('Выбор (Enter — 4, Lite)', clean(seen.decode('utf-8', 'replace')))
            out = st.keys(['2\n', 'example.com\n'], seconds=60)
            cfg = st.config()
            try:
                os.killpg(first.pid, 9)
            except OSError:
                pass
            first.wait(timeout=30)
            # Первый убит (оборвалась сессия) — его отметка не держит следующий заход
            after = st.pipe(seconds=120)
        finally:
            try:
                os.killpg(first.pid, 9)
            except OSError:
                pass
            os.close(master)
            st.close()
        flat = ' '.join(out.split())
        self.assertIn('Установка уже идёт в другом окне (процесс %d) — вторую не начинаю и ничего не трогаю.'
                      % first.pid, flat, out[-3000:])
        self.assertNotIn('Как выбрать: 1 —', out)
        self.assertEqual(cfg.get('profiles', {}), {})
        self.assertNotIn('Установка уже идёт', after)
        self.assertIn('Ввод оборвался', after)


def app_containers_stand(stop_legacy=False):
    """Стенд «сервер, где уже стоят контейнеры приложения Amnezia» (фикстуры tests/fixtures/import).

    amnezia-awg2 (2.0, двое), legacy amnezia-awg (1.0, один) и amnezia-xray. stop_legacy — amnezia-awg
    остановлен (файл .state), как контейнер, который приложение или человек погасили.
    """
    st = Stand()
    fx = ROOT / 'tests' / 'fixtures' / 'import'
    cfs = st.tmp / 'cfs'
    shutil.copytree(fx / 'fs', cfs)
    if stop_legacy:
        (cfs / 'amnezia-awg' / '.state').write_text('exited\n', encoding='utf-8')
    st.env['GOAMNEZIA_DOCKER_PS_FIXTURE'] = str(fx / 'docker_ps.txt')
    st.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
    return st


class WizardFindsAmneziaContainers(unittest.TestCase):
    """OWN-1: контейнеры Amnezia на сервере — первым экраном мастера, до выбора режима.

    Живая жалоба владельца 25.09.2026: он ставил goAmnezia на сервер с контейнерами приложения Amnezia —
    установщик их «не увидел», подхват не предложил, и владелец от установки отказался. Поиск шёл на шаге
    6а, после вопросов о режиме, домене, профилях и портах; остановленных контейнеров он не видел вовсе, а
    контейнеры без людей молча пропускал.
    """

    def test_adoption_comes_first_and_the_wizard_goes_on_with_adopted_profiles(self):
        st = app_containers_stand(stop_legacy=True)
        try:
            # 1 — подхватить; 4 — Lite; Enter — профили; Enter — порты; n — бот; Enter; 0 — выход.
            out = st.keys(['1\n', '4\n', '\n', '\n', 'n\n', '\n', '0\n'])
            cfg = st.config()
            names = [u['name'] for u in st.users().get('users', [])]
        finally:
            st.close()
        flat = ' '.join(out.split())
        found = out.find('На сервере уже есть контейнеры Amnezia')
        ask = out.find('Подхватить контейнеры AmneziaWG (2)?')
        mode = out.find('Как выбрать: 1 —')
        self.assertGreaterEqual(found, 0, out[:3000])
        self.assertGreater(ask, found, out[:3000])
        self.assertGreater(mode, ask, 'вопрос о подхвате должен идти раньше выбора режима')
        # В списке — все контейнеры amnezia-*: и остановленный, и чужого протокола, с причиной.
        screen = ' '.join(out[found:ask].split())
        self.assertRegex(screen, r'amnezia-awg\s', screen)
        for part in ('amnezia-awg2', 'amnezia-xray', 'остановлен', 'пользователей: 2',
                     'не подхватывается', 'после подхвата запущу'):
            self.assertIn(part, screen)
        profiles = cfg.get('profiles', {})
        self.assertEqual(profiles.get('awg2', {}).get('preset'), 'imported', out[-3000:])
        self.assertEqual(profiles['awg2']['port'], 38123)
        self.assertEqual(profiles.get('awg1', {}).get('container'), 'amnezia-awg')
        self.assertIn('awg3', profiles)
        self.assertNotEqual(profiles['awg3'].get('preset'), 'imported')
        self.assertTrue(cfg.get('installed'))
        # Остановленный контейнер запущен, подхваченные профили на галочках замкнуты, их порты не спрашиваются.
        self.assertIn('Контейнер amnezia-awg стоял — запущен', flat)
        self.assertIn('подхвачен из приложения Amnezia — мастер его не переустанавливает', flat)
        self.assertIn('подхвачен — порт не меняем', flat)
        # Экран портов: подхваченный — подписью «из приложения» и своим портом.
        # Порт стоит в самой фразе или колонкой — подпись без него (решение владельца 15.10, DUP-LINE-01).
        self.assertIn('\n    AmneziaWG 2.0 · из приложения 38123  подхвачен — порт не меняем', out)
        self.assertIn('\n    AmneziaWG 1.0 · из приложения 51820  подхвачен — порт не меняем', out)
        # Люди приложения стали пользователями, а свой первый пользователь всё равно создан — с ключом.
        for name in ('iPhone_Anny', 'office-router', 'Keenetic_doma', 'admin'):
            self.assertIn(name, names)
        # Ключ мастера — самого сильного стоящего профиля: 3.1 (NC-27)
        self.assertRegex(flat, r'Конфигурация admin для профиля AmneziaWG 3\.1 · порт \d+')   # без голого awg3
        # Контейнер приложения не пересобран и не снесён, снимок до подхвата снят.
        self.assertNotRegex(out, r'docker (rm|stop)[^\n]*amnezia-awg2\b')
        self.assertNotRegex(out, r'docker build[^\n]*amnezia-awg2\b')
        self.assertIn('Снимок контейнера до подхвата', flat)

    def test_declining_says_what_it_means_and_changes_nothing(self):
        st = app_containers_stand()
        conf = st.tmp / 'cfs' / 'amnezia-awg2' / 'opt' / 'amnezia' / 'awg' / 'awg0.conf'
        before = conf.read_bytes()
        try:
            out = st.keys(['0\n', '0\n'])
            cfg = st.config()
            after = conf.read_bytes()
        finally:
            st.close()
        self.assertEqual(after, before)
        self.assertNotRegex(out, r'docker (rm|stop|build|run)[^\n]*amnezia-awg2\b')
        flat = ' '.join(out.split())
        self.assertIn('Без подхвата goAmnezia не поставить', flat, out[-3000:])
        self.assertIn('Ничего не установлено и не изменено', flat)
        # Необязательный профиль на месте контейнера с людьми назван тоже.
        self.assertIn('Профиль AmneziaWG 1.0 не ставлю: его место занимает контейнер amnezia-awg, пользователей в нём: 1.',
                      flat)   # по линии контейнера (DUP-LINE-01), без имени места
        self.assertNotIn('Как выбрать: 1 —', out)
        self.assertNotEqual(cfg.get('installed'), True)
        self.assertEqual(cfg.get('profiles', {}), {})

    def test_without_a_terminal_nothing_is_adopted_or_installed(self):
        st = app_containers_stand()
        try:
            out = st.pipe(seconds=180)
            cfg = st.config()
        finally:
            st.close()
        self.assertIn('На сервере уже есть контейнеры Amnezia', out)
        self.assertNotEqual(cfg.get('installed'), True, out[-3000:])
        self.assertEqual(cfg.get('profiles', {}), {})


def app_containers_one_subnet_stand():
    """Сервер, как его ставит само приложение Amnezia: старый amnezia-awg (wg0) и amnezia-awg2 — оба на 10.8.1.0/24
    (protocols::wireguard::defaultSubnetAddress в amnezia-client), старый в docker ps первым, в обоих люди."""
    st = Stand()
    fx = ROOT / 'tests' / 'fixtures' / 'import'
    cfs = st.tmp / 'cfs'
    shutil.copytree(fx / 'fs', cfs)
    shutil.rmtree(cfs / 'amnezia-xray')
    wg0 = cfs / 'amnezia-awg' / 'opt' / 'amnezia' / 'awg' / 'wg0.conf'
    wg0.write_text(wg0.read_text(encoding='utf-8').replace('10.8.5.', '10.8.1.'), encoding='utf-8')
    ps = st.tmp / 'docker_ps.txt'
    ps.write_text('amnezia-awg 0.0.0.0:51820->51820/udp\n'
                  'amnezia-awg2 0.0.0.0:38123->38123/udp, :::38123->38123/udp\n', encoding='utf-8')
    st.env['GOAMNEZIA_DOCKER_PS_FIXTURE'] = str(ps)
    st.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
    return st


class WizardAppContainersOnOneSubnet(unittest.TestCase):
    """SUBNET-01 (жалоба владельца 06.10): «при подхвате старого протокола и попытке добавить новый почему-то
    пересекаются подсети».

    Приложение ставит любой свой контейнер AmneziaWG на 10.8.1.0/24. Было: старый подхватывался, а amnezia-awg2 —
    отказ «подсеть пересекается», и мастер на месте 2.0 с людьми останавливался («без подхвата goAmnezia не
    поставить»), а сменить подсеть старому нельзя. Теперь оба подхвачены как есть, мастер идёт дальше, новый профиль
    встаёт на свою подсеть, а выход из пересечения назван: перенести awg2 на свободную подсеть.
    """

    def test_both_are_adopted_the_wizard_goes_on_and_names_the_way_out(self):
        st = app_containers_one_subnet_stand()
        try:
            # 1 — подхватить; 4 — Lite; Enter — профили; Enter — порты; n — бот; Enter; 0 — выход.
            out = st.keys(['1\n', '4\n', '\n', '\n', 'n\n', '\n', '0\n'])
            cfg = st.config()
        finally:
            st.close()
        flat = ' '.join(out.split())
        profiles = cfg.get('profiles', {})
        self.assertNotIn('Без подхвата goAmnezia не поставить', flat, out[-3000:])
        self.assertEqual(profiles.get('awg1', {}).get('container'), 'amnezia-awg', out[-3000:])
        self.assertEqual(profiles.get('awg2', {}).get('preset'), 'imported', out[-3000:])
        self.assertEqual(profiles['awg1'].get('subnet'), '10.8.1.0/24')
        self.assertEqual(profiles['awg2'].get('subnet'), '10.8.1.0/24')
        # Новый профиль мастера — на своей подсети, а не на подсети подхваченных.
        self.assertIn('awg3', profiles, out[-3000:])
        self.assertNotEqual(profiles['awg3'].get('preset'), 'imported')
        self.assertNotEqual(profiles['awg3'].get('subnet'), '10.8.1.0/24')
        self.assertTrue(cfg.get('installed'))
        # Пересечение названо, и выход — перенести awg2 (старому подсеть не сменить) на свободную подсеть.
        self.assertIn('Подсеть 10.8.1.0/24 профиля AmneziaWG 2.0 · порт 38123 · из приложения пересекается с подсетью '
                      'AmneziaWG 1.0 · порт 51820 · из приложения.', flat)
        self.assertIn('перенесите AmneziaWG 2.0 · порт 38123 · из приложения на свободную подсеть 10.8.2.0/24', flat)
        self.assertNotRegex(out, r'docker (rm|stop|build)[^\n]*amnezia-awg2?\b')


class UserCardImportedKey(unittest.TestCase):
    """Карточка и импортированный ключ: правда до нажатия, без ложных тревог.

    У пира, пришедшего из контейнера, приватной части на сервере нет, и выдача
    ключа по нему заведомо отказывает. Карточка обязана сказать это заранее — но
    ровно там, где это правда: первая версия проверки смотрела на отсутствие
    privkey в СПИСКЕ, а список идёт через публичный фильтр, который приватный
    ключ вырезает, — и предупреждение висело у всех подряд.
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def _card(self):
        out = self.stand.keys(['1\n', '1\n', '0\n', '0\n', '0\n'])
        i = out.rfind('Пользователь ')
        return out[i:i + 1200] if i > 0 else out[-1200:]

    def test_normal_user_has_no_imported_warning(self):
        card = self._card()
        self.assertNotIn('выдан в приложении Amnezia', ' '.join(card.split()), card)
        self.assertIn('Ключ: QR, файл, ссылка', card)   # карточка собрана: форматы — внутри пункта

    def test_imported_peer_says_so_and_offers_reissue(self):
        # Стенд общий на класс, а методы идут по алфавиту — правку возвращаем,
        # иначе соседняя проверка увидит чужое состояние и упадёт на ровном месте.
        u = self.stand.dir / 'users.json'
        before = u.read_text(encoding='utf-8')
        try:
            data = json.loads(before)
            for user in data['users']:
                for peer in user.get('peers', {}).values():
                    peer['privkey'] = ''
            u.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            card = self._card()
            # Перевыпуск живёт в «Профили пользователя»: 1 — список, 1 — карточка,
            # 2 — профили. Проверяем, что оттуда он доступен.
            profiles = self.stand.keys(['1\n', '1\n', '2\n', '0\n', '\n', '0\n', '0\n'])
        finally:
            u.write_text(before, encoding='utf-8')
        flat = ' '.join(card.split())
        # Без недосказанности: где ключ, что он работает, почему его нет здесь и два выхода.
        for part in ('выдан в приложении Amnezia', 'У пользователя всё работает',
                     'показать или отправить этот ключ отсюда нельзя', 'Привязать старый ключ', 'Перевыпустить ключ'):
            self.assertIn(part, flat, card)
        self.assertIn('Перевыпустить ключ на профиле', profiles, profiles[-800:])
        self.assertIn('Привязать старый ключ', profiles, profiles[-800:])

    def test_attach_old_key_from_pasted_conf(self):
        # Ключ администратора «уходил» ему файлом .conf; на сервере его стираем —
        # так выглядит пир из приложения. Вставка того .conf возвращает ключ, а
        # у человека ничего не меняется: открытый ключ пира тот же.
        u = self.stand.dir / 'users.json'
        before = u.read_text(encoding='utf-8')
        try:
            data = json.loads(before)
            admin = next(x for x in data['users'] if x['name'] == 'admin')
            priv = admin['peers']['awg2']['privkey']
            srv = self.stand.config()['profiles']['awg2']['server_pubkey']
            conf = ('[Interface]\nPrivateKey = %s\nAddress = 10.8.2.1/32\n\n[Peer]\nPublicKey = %s\n'
                    'AllowedIPs = 0.0.0.0/0\n' % (priv, srv))
            for peer in admin['peers'].values():
                peer['privkey'] = ''
            u.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            # 1 — пользователи, 1 — admin, 1 — «Ключ», 1 — «Привязать старый ключ» (все ключи выданы в приложении —
            # на экране «Ключ» только привязка и перевыпуск, KEY-APP-02), вставка, Enter — пауза, дальше назад до выхода.
            out = self.stand.keys(['1\n', '1\n', '1\n', '1\n', conf, '\n', '0\n', '0\n', '0\n', '0\n'])
            after = json.loads(u.read_text(encoding='utf-8'))
        finally:
            u.write_text(before, encoding='utf-8')
        flat = ' '.join(out.split())
        self.assertIn('QR, файл и ссылки отсюда не выдать: все ключи пользователя выданы в приложении Amnezia. Работает '
                      'одно из двух: 1) Привязать старый ключ (ссылка vpn:// или .conf, которые вы отправляли) '
                      '2) Перевыпустить ключ на профиле 0) « Назад', flat, out[-2000:])
        self.assertIn('Привязка старого ключа: admin', flat, out[-2000:])
        self.assertRegex(flat, r'Старый ключ admin на профиле AmneziaWG 2\.0 · порт \d+( · из приложения)? привязан', out[-2000:])
        self.assertNotIn(priv, out)                     # ввод без эха: ключа на экране нет
        got = next(x for x in after['users'] if x['name'] == 'admin')['peers']
        self.assertEqual(got['awg2']['privkey'], priv)
        self.assertEqual(got['awg3']['privkey'], '')    # второй профиль так и остался без ключа

    def test_attach_old_key_from_a_long_vpn_link(self):
        # Ссылка vpn:// линии 2.0 с сигнатурными пакетами I1–I5 — длиннее строки канонического терминала (4095 байт
        # в Linux, 1024 в macOS): вставка обрезалась, и привязка отвечала «не удалось прочитать ключ» (TK-3,
        # TAIL-1). Строку теперь собирает сам bash (ga_read_paste) — ссылка доходит целиком, одной вставкой.
        u = self.stand.dir / 'users.json'
        before = u.read_text(encoding='utf-8')
        try:
            data = json.loads(before)
            admin = next(x for x in data['users'] if x['name'] == 'admin')
            priv = admin['peers']['awg2']['privkey']
            srv = self.stand.config()['profiles']['awg2']['server_pubkey']
            rnd = random.Random(38)
            ipk = ''.join('I%d = <b 0x%s>\n' % (i, rnd.randbytes(1000).hex()) for i in range(1, 6))
            conf = ('[Interface]\nPrivateKey = %s\nAddress = 10.8.2.1/32\nJc = 4\nJmin = 10\nJmax = 50\n%s\n'
                    '[Peer]\nPublicKey = %s\nAllowedIPs = 0.0.0.0/0\nEndpoint = 203.0.113.10:39514\n' % (priv, ipk, srv))
            enc = subprocess.run([sys.executable, str(ROOT / 'lib' / 'awgkeys.py'), 'vpn-encode'],
                                 input=json.dumps({'conf_text': conf, 'line': '2.0', 'container': 'amnezia-awg2'}),
                                 capture_output=True, text=True, timeout=60)
            vpn = enc.stdout.strip()
            self.assertTrue(vpn.startswith('vpn://'), enc.stderr)
            self.assertGreater(len(vpn), 6000)
            for peer in admin['peers'].values():
                peer['privkey'] = ''
            u.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            # 1 — пользователи, 1 — admin, 1 — «Ключ», 1 — «Привязать старый ключ» (KEY-APP-02), ссылка одной вставкой,
            # Enter — пауза, дальше назад до выхода. Клавиша после вставки — только когда вставка кончилась
            # (пауза вставки — секунда, а обвязка жмёт через 0,8 с тишины: Enter ушёл бы строкой вставки).
            out = self.stand.keys(['1\n', '1\n', '1\n', '1\n', vpn + '\n', ('until', 'Получено строк'),
                                   '\n', '0\n', '0\n', '0\n', '0\n'])
            after = json.loads(u.read_text(encoding='utf-8'))
        finally:
            u.write_text(before, encoding='utf-8')
        flat = ' '.join(out.split())
        self.assertIn('Получено строк: 1', flat, out[-2000:])
        self.assertRegex(flat, r'Старый ключ admin на профиле AmneziaWG 2\.0 · порт \d+( · из приложения)? привязан', out[-2000:])
        self.assertNotIn(vpn[6:200], out)               # ввод без эха: ссылки на экране нет
        got = next(x for x in after['users'] if x['name'] == 'admin')['peers']
        self.assertEqual(got['awg2']['privkey'], priv)


class UserExpiryWording(unittest.TestCase):
    """Ноль в сроке и лимите — это «бессрочно» и «без лимита», а не «0 дней».

    Экран печатал «Срок пользователя anna: 0 дней.» и «Лимит: 0 ГБ.» — по букве
    верно, по смыслу наоборот. Заодно вопрос показывает текущее значение: без
    него человек, зашедший посмотреть, снимал срок одним Enter, не узнав, какой
    он был.
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_zero_days_is_called_unlimited_and_current_value_is_shown(self):
        # Второй заход ставит «бессрочно» пустым Enter: ноль на этом вопросе —
        # выход, как и везде в меню.
        out = self.stand.keys(['1\n', '1\n', '4\n', '5\n', '\n', '4\n', '\n', '\n', '0\n', '0\n', '0\n'])
        self.assertIn('Срок в днях (сейчас: бессрочно; Enter — бессрочно, 0 — назад)', out)
        self.assertIn('Срок пользователя admin: 5 дней.', out)
        # второй заход: вопрос уже знает про поставленные пять дней — сначала сколько осталось, в скобках часы
        # сервера с пометкой, без UTC (NC-11); карточка — так же
        flat = ' '.join(out.split())
        self.assertRegex(flat, r'Срок в днях \(сейчас: истекает через \d+ дн\. \(20\d\d-\d\d-\d\d \d\d:\d\d по времени сервера\);')
        self.assertRegex(flat, r'Срок: истекает через \d+ дн\. \(20\d\d-\d\d-\d\d \d\d:\d\d по времени сервера\)')
        self.assertNotIn('UTC', out)
        self.assertIn('Срок пользователя admin: бессрочно.', out)
        self.assertNotIn('0 дней', out)

    def test_zero_does_not_change_anything(self):
        """Ноль на этих вопросах — выход, а не «снять ограничение».

        Человек, зашедший посмотреть и нажавший привычный ноль, молча снимал
        чужой срок: ga_ask_optional с числовым выражением принимал ноль как
        значение, а «назад» по нулю делает только ga_ask.
        """
        self.stand.keys(['1\n', '1\n', '4\n', '7\n', '\n', '0\n', '0\n', '0\n'])
        before = json.loads((self.stand.dir / 'users.json').read_text(encoding='utf-8'))
        was = before['users'][0].get('expires', '')
        self.assertTrue(was, 'срок не поставился — проверять нечего')
        out = self.stand.keys(['1\n', '1\n', '4\n', '0\n', '\n', '0\n', '0\n', '0\n'])
        after = json.loads((self.stand.dir / 'users.json').read_text(encoding='utf-8'))
        self.assertEqual(after['users'][0].get('expires', ''), was, out[-2500:])
        self.assertIn('Отменено', out)

    def test_zero_quota_is_called_no_limit(self):
        # «Без ограничения» — тоже пустой Enter, ноль отсюда выходит.
        out = self.stand.keys(['1\n', '1\n', '3\n', '10\n', '\n', '3\n', '\n', '\n', '0\n', '0\n', '0\n'])
        # NC6-06: лимит — за всё время, без сброса по месяцам: так сказано и в вопросе, и в итоге
        flat = ' '.join(out.split())
        self.assertIn('Лимит трафика в ГБ — за всё время, без сброса по месяцам (сейчас', flat)
        self.assertIn('Лимит пользователя admin: 10 ГБ за всё время, без сброса по месяцам.', flat)
        self.assertIn('Лимит пользователя admin: без ограничения.', out)
        self.assertNotIn(': 0 ГБ', out)


class UsersScreen(unittest.TestCase):
    """Список пользователей: номера, страницы, добавление без лишних вопросов."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_list_is_numbered_and_card_opens_by_number(self):
        out = self.stand.keys(['1\n', '1\n', '0\n', '0\n', '0\n'])
        # Список — между заголовком экрана и строкой-подсказкой. Раньше брался хвост после
        # последнего «Пользователи», но с «0 — назад» без «Нажмите Enter» главное меню
        # перерисовывается сразу, и последним становится его пункт «1) Пользователи».
        listing = out.split('Номер строки — открыть карточку')[0].split('Пользователи')[-1]
        self.assertRegex(listing, r'\s+1\)\s+●\s+admin')
        # Управление только цифрами: пункты идут сразу за последней строкой списка. Подсказка с «удалить несколько»
        # длиннее строки окна и переносится — сверяем без переносов.
        self.assertRegex(' '.join(out.split()), r'Номер строки — открыть карточку; \d+ — добавить, \d+ — несколько сразу, \d+ — удалить несколько, \d+ — найти по имени, \d+ — раздачи, 0 — назад\.')
        self.assertRegex(out, r'\n\s+%d\) Добавить пользователя' % user_items(self.stand)[0])
        # Карточка того самого пользователя открылась
        self.assertIn('Пользователь admin', out)

    def test_name_input_still_works(self):
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[1], 'admin\n', '0\n', '0\n', '0\n'])
        self.assertIn('Пользователь admin', out)

    def test_zero_at_the_quota_question_goes_back_and_creates_nobody(self):
        """Ноль — «назад» и здесь: в карточке этот уговор уже был, а при
        заведении того же человека ноль молча значил «без лимита»."""
        before = {u['name'] for u in self.stand.users().get('users', [])}
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'zeroman\n', '\n',
                               '0\n', '0\n', '0\n', '0\n'])
        after = {u['name'] for u in self.stand.users().get('users', [])}
        self.assertNotIn('zeroman', after, out[-3000:])
        self.assertEqual(before, after, out[-3000:])

    def test_add_user_has_no_display_name_question(self):
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'anna\n', '\n', '\n', '\n',
                               '\n', '0\n', '0\n'])
        self.assertNotIn('Отображаемое имя', out)
        self.assertNotIn('Профили через запятую', out)
        self.assertIn('Каким профилям выдать ключ?', out)
        # Ноль значит «назад» во всём продукте — вопрос обязан это сказать; лимит — за всё время (NC6-06).
        self.assertIn('Лимит трафика в ГБ — за всё время, без сброса по месяцам (Enter — без лимита, 0 — назад)',
                      ' '.join(out.split()))
        self.assertIn('Срок в днях (Enter — бессрочно, 0 — назад)', out)
        anna = [u for u in self.stand.users().get('users', []) if u['name'] == 'anna']
        self.assertEqual(len(anna), 1, out[-3000:])
        self.assertEqual(anna[0]['label'], 'anna')
        self.assertEqual(anna[0]['quota_bytes'], 0)
        self.assertEqual(anna[0]['expires'], '')
        # По умолчанию отмечены ВСЕ установленные профили
        self.assertEqual(sorted(anna[0]['peers']), ['awg2', 'awg3'])

    SUGGESTED = re.compile(r'^\s*🎲 (.+?) · ([a-z0-9_]+)\s*$', re.M)

    def test_random_name_enter_offers_r_gives_another_enter_takes(self):
        """«🎲 Случайное имя» (C47-1, docs/MODES_047.md «Имена ключей»): пустой Enter в поле имени — вариант
        «прилагательное + столица», r — другой, Enter — взять: id латиницей, подпись — вариант на языке меню."""
        names = {n['id']: n for n in json.loads((ROOT / 'lib' / 'names_capitals.json').read_text(encoding='utf-8'))}
        before = {u['name'] for u in self.stand.users().get('users', [])}
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], '\n', 'r\n', '\n', '\n', '\n', '\n',
                               '\n', '0\n', '0\n'])
        self.assertIn('Enter или ? — 🎲 случайное имя', out)
        self.assertIn('Enter — взять, r — другое, или введите своё', out)
        shown = self.SUGGESTED.findall(out)
        self.assertEqual(len(shown), 2, out[-3000:])
        self.assertNotEqual(shown[0][1], shown[1][1], '«r — другое» даёт другой вариант')
        for label, ident in shown:
            self.assertEqual(label, names[ident]['ru'], 'подпись — на языке меню')
        new = [u for u in self.stand.users().get('users', []) if u['name'] not in before]
        self.assertEqual([(u['name'], u['label']) for u in new], [(shown[1][1], shown[1][0])], out[-3000:])

    def test_question_mark_offers_a_name_and_an_own_name_still_wins(self):
        before = {u['name'] for u in self.stand.users().get('users', [])}
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], '?\n', 'olga\n', '\n', '\n', '\n',
                               '\n', '0\n', '0\n'])
        self.assertEqual(len(self.SUGGESTED.findall(out)), 1, out[-3000:])
        new = [u for u in self.stand.users().get('users', []) if u['name'] not in before]
        self.assertEqual([(u['name'], u['label']) for u in new], [('olga', 'olga')], out[-3000:])

    def test_bulk_add_with_a_prefix_lists_the_people_and_says_where_the_keys_are(self):
        """«Добавить сразу несколько» (просьба владельца 03.10): сколько → те же галочки профилей → префикс →
        подтверждение; заведены team-01…03 во всех установленных профилях, на экране — их список и где ключи."""
        before = {u['name'] for u in self.stand.users().get('users', [])}
        bulk = user_items(self.stand)[3]
        out = self.stand.keys(['1\n', '%d\n' % bulk, '3\n', '\n', 'team\n', '1\n', ('until', 'Добавлены (3)'),
                               '\n', '0\n', '0\n'], seconds=120)
        flat = ' '.join(out.split())
        self.assertRegex(out, r'\n\s+%d\) Добавить сразу несколько \(до 100\)' % bulk)
        self.assertIn('Сколько пользователей добавить (1–100)', flat)
        self.assertIn('Каким профилям выдать ключ?', out)
        ports = {p: v['port'] for p, v in self.stand.config()['profiles'].items()}
        self.assertIn('Число пользователей: 3, профили: AmneziaWG 3.1 · порт %d, AmneziaWG 2.0 · порт %d, имена вида team-01. '
                      'Добавить?' % (ports['awg3'], ports['awg2']), flat, out[-3000:])
        self.assertIn('Добавлены (3):', out)
        self.assertIn('Ключи — в карточке каждого', flat)
        new = sorted((u['name'], tuple(sorted(u['peers']))) for u in self.stand.users().get('users', [])
                     if u['name'] not in before)
        self.assertEqual(new, [('team-01', ('awg2', 'awg3')), ('team-02', ('awg2', 'awg3')),
                               ('team-03', ('awg2', 'awg3'))], out[-3000:])

    def test_bulk_add_zero_goes_back_and_creates_nobody(self):
        before = {u['name'] for u in self.stand.users().get('users', [])}
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[3], '0\n', '0\n', '0\n', '0\n'])
        after = {u['name'] for u in self.stand.users().get('users', [])}
        self.assertEqual(before, after, out[-3000:])
        self.assertNotIn('Добавлены (', out)

    def _bridge(self, *args):
        """Мост стенда напрямую — завести людей для удаления, не проходя мастер добавления ещё раз."""
        p = subprocess.run([BASH, str(ROOT / 'install.sh')] + list(args) + ['--json'], env=self.stand.env,
                           cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_bulk_delete_by_prefix_shows_who_and_takes_the_number(self):
        """«Удалить сразу несколько» (просьба владельца 03.10): по префиксу → список тех, кого удалим → подтверждение
        числом людей → итог; gone-01…03 стёрты, остальные на месте."""
        self.assertEqual(self._bridge('--action=users-add-bulk', '--count=3', '--prefix=gone')['status'], 'success')
        before = {u['name'] for u in self.stand.users().get('users', [])}
        bulk_del = user_items(self.stand)[4]
        out = self.stand.keys(['1\n', '%d\n' % bulk_del, '1\n', 'gone\n', ('until', 'для подтверждения'), '3\n',
                               ('until', 'Удалены (3)'), '\n', '0\n', '0\n'], seconds=120)
        flat = ' '.join(out.split())
        self.assertRegex(out, r'\n\s+%d\) Удалить сразу несколько' % bulk_del)
        self.assertIn('Кого удалить?', out)
        self.assertIn('По номерам строк списка — например 3-7,12', flat)
        self.assertIn('Удалю (3):', out)
        for name in ('gone-01', 'gone-02', 'gone-03'):
            self.assertIn(name, out)
        self.assertIn('Ключи этих пользователей перестанут работать сразу и навсегда', flat)
        self.assertIn('Наберите «3» для подтверждения', flat)
        self.assertIn('Удалены (3):', out)
        after = {u['name'] for u in self.stand.users().get('users', [])}
        self.assertEqual(before - after, {'gone-01', 'gone-02', 'gone-03'}, out[-3000:])

    def test_bulk_delete_by_rows_needs_the_right_number(self):
        """Номера строк списка (3-7,12): не то число в подтверждении — никого не удалено; верное — удалены ровно
        эти строки."""
        self.assertEqual(self._bridge('--action=users-add-bulk', '--count=2', '--prefix=rw')['status'], 'success')
        names = [u['name'] for u in self.stand.users().get('users', [])]
        rows = '%d-%d' % (names.index('rw-01') + 1, names.index('rw-02') + 1)
        bulk_del = user_items(self.stand)[4]
        out = self.stand.keys(['1\n', '%d\n' % bulk_del, '2\n', rows + '\n', ('until', 'для подтверждения'), '5\n',
                               '\n', '0\n', '0\n'])
        self.assertIn('Удалю (2):', out)
        self.assertIn('Набрано не то — отменено, ничего не сделано.', out)
        self.assertEqual([n for n in (u['name'] for u in self.stand.users().get('users', [])) if n.startswith('rw-')],
                         ['rw-01', 'rw-02'], out[-3000:])
        out = self.stand.keys(['1\n', '%d\n' % bulk_del, '2\n', rows + '\n', ('until', 'для подтверждения'), '2\n',
                               ('until', 'Удалены (2)'), '\n', '0\n', '0\n'])
        self.assertIn('Удалены (2):', out)
        left = [u['name'] for u in self.stand.users().get('users', [])]
        self.assertEqual([n for n in left if n.startswith('rw-')], [], out[-3000:])
        self.assertEqual(len(left), len(names) - 2)

    def test_bulk_delete_zero_goes_back_and_deletes_nobody(self):
        before = {u['name'] for u in self.stand.users().get('users', [])}
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[4], '0\n', '\n', '0\n', '0\n'])
        after = {u['name'] for u in self.stand.users().get('users', [])}
        self.assertEqual(before, after, out[-3000:])
        self.assertIn('Кого удалить?', out)
        self.assertNotIn('Удалены (', out)


class AgentsPortQuestion(unittest.TestCase):
    """Порт доступа агентов: переспрос и предложение из низкого диапазона.

    Опечатка в номере порта выбрасывала из сценария целиком — второй попытки не
    давали, и человек шёл искать пункт меню заново (DEFECTS §1.5). Здесь
    настоящий экран в pty: два негодных ответа подряд, потом верный.
    """

    PROMPT = 'TCP-порт HTTP-транспорта'

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)
        # Настройки (5) → «Веб-панель, админы бота, ИИ-агенты» (3) → «Доступы агентов» (3) →
        # транспорт. Доступов ещё нет, поэтому пункты начинаются с первого:
        # 1 — выдать, 2 — транспорт, 3 — журнал.
        cls.out = cls.stand.keys(['5\n', '3\n', '3\n', '2\n', '77\n', 'abc\n', '4433\n',
                                  '1\n', '0\n', '0\n', '0\n', '0\n'])

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_bad_port_is_asked_again(self):
        asked = self.out.count(self.PROMPT)
        self.assertGreaterEqual(asked, 3, self.out[-3000:])
        self.assertGreaterEqual(self.out.count('Нужен номер порта 1024–65535.'), 2,
                                self.out[-3000:])

    def test_the_third_answer_is_the_one_that_counts(self):
        mcp = self.stand.config().get('mcp') or {}
        self.assertEqual(mcp.get('public_port'), 4433, self.out[-3000:])
        self.assertTrue(mcp.get('http_enabled'), self.out[-3000:])

    def test_suggested_port_is_below_ten_thousand(self):
        m = re.search(re.escape(self.PROMPT) + r' \(Enter — (\d+)\)', self.out)
        self.assertIsNotNone(m, self.out[-3000:])
        self.assertTrue(1024 <= int(m.group(1)) <= 9999, m.group(1))


# Токен нарочно узнаваемый: по нему тест и ищет утечку в аргументах команды.
FAKE_BOT_TOKEN = '123456789:' + 'A' * 35
# Ответ заглушки curl вместо api.telegram.org: одно сообщение от «Виталия».
FAKE_CURL = '''#!/usr/bin/env bash
# Заглушка curl для проверки 1c.13: записывает argv и stdin, отвечает как Telegram.
{ printf '%s\\t' "$@"; printf '\\n'; } >> "$GA_CURL_ARGV"
cfg=""
for a in "$@"; do
    if [ "$a" = "-K" ]; then cfg=$(cat); printf '%s\\n' "$cfg" >> "$GA_CURL_STDIN"; break; fi
done
all="$* $cfg"
case "$all" in
    *getMe*)      printf '{"ok":true,"result":{"username":"stand_bot"}}' ;;
    *getUpdates*) printf '{"ok":true,"result":[{"update_id":1,"message":{"from":{"id":777000111,"is_bot":false,"first_name":"Виталий","username":"vitalij"}}}]}' ;;
    *)            printf '{}' ;;
esac
'''


# Подставной bash для установщика бота: install.sh зовёт `bash install_bot.sh --auto` и `--pair` по имени из
# PATH. Вызов установщика записываем (окружение и argv), остальное — настоящему bash. Как настоящий установщик,
# --auto кладёт .env с переданными админами (пусто — нет админов), и меню видит, что привязывать есть кого.
FAKE_BASH = '''#!/bin/sh
case "$1" in
    */install_bot.sh)
        printf 'CLAIM=%s PAIR=%s IDS=[%s] TOKEN=%s ARGV=%s\\n' "${GOAMNEZIA_ADMIN_CLAIM:-}" "${GOAMNEZIA_ADMIN_PAIR:-}" \\
            "${ALLOWED_IDS:-}" "${BOT_TOKEN:+set}" "$*" >> "$GA_FAKE_BOT_LOG"
        if [ "$2" = "--auto" ] && [ -n "${BOT_TOKEN:-}" ] && [ -n "${GOAMNEZIA_BOT_DIR:-}" ]; then
            mkdir -p "$GOAMNEZIA_BOT_DIR" && printf 'BOT_TOKEN=%s\\nALLOWED_IDS=%s\\n' "$BOT_TOKEN" "${ALLOWED_IDS:-}" \\
                > "$GOAMNEZIA_BOT_DIR/.env"
        fi
        exit 0 ;;
esac
exec "@REAL_BASH@" "$@"
'''


class BotAdminByMessage(unittest.TestCase):
    """Первого администратора бота привязывают СООБЩЕНИЕМ с подтверждением в SSH (решение владельца 27.09,
    docs/BOT.md «Контракт привязки», C47-1).

    SEC-B00: имя бота видно всем, и «первый написавший» администратором стать не должен. Прежнее «напишу боту
    сам» ловило первого написавшего в getUpdates, потом Enter отдавал назначение одноразовому коду. Теперь Enter
    — привязка сообщением: мастер ставит бота с GOAMNEZIA_ADMIN_PAIR=1 (кода нет — терминал здесь) и сразу
    открывает окно привязки тем же установщиком (--pair); сам он очередь бота не читает. Тот же вызов — у пункта
    «Telegram-бот → Администраторы → Привязать по сообщению». Токен установщику — окружением, не аргументом.
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        fakebin = cls.stand.tmp / 'bin'
        fakebin.mkdir()
        curl = fakebin / 'curl'
        curl.write_text(FAKE_CURL, encoding='utf-8')
        curl.chmod(0o755)
        try:
            require_exec(curl)
        except unittest.SkipTest:
            cls.stand.close()
            raise
        fbash = fakebin / 'bash'
        fbash.write_text(FAKE_BASH.replace('@REAL_BASH@', shutil.which(BASH) or BASH), encoding='utf-8')
        fbash.chmod(0o755)
        cls.argv_log = cls.stand.tmp / 'curl-argv.log'
        cls.stdin_log = cls.stand.tmp / 'curl-stdin.log'
        cls.bot_log = cls.stand.tmp / 'fake-bot.log'
        cls.stand.env['PATH'] = str(fakebin) + os.pathsep + cls.stand.env['PATH']
        cls.stand.env['GA_CURL_ARGV'] = str(cls.argv_log)
        cls.stand.env['GA_CURL_STDIN'] = str(cls.stdin_log)
        cls.stand.env['GA_FAKE_BOT_LOG'] = str(cls.bot_log)
        # Lite → профили по умолчанию → порт по умолчанию → ДА, ставим бота →
        # токен → Enter (привязать сообщением) → Enter (пауза) → выход.
        cls.out = cls.stand.keys(['4\n', '\n', '\n', 'y\n', FAKE_BOT_TOKEN + '\n',
                                  '\n', '\n', '0\n'])

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def _read(self, path):
        return path.read_text(encoding='utf-8') if path.exists() else ''

    def test_enter_binds_the_admin_by_message_right_after_the_install(self):
        calls = self._read(self.bot_log).splitlines()
        self.assertGreaterEqual(len(calls), 2, self.out[-3000:])
        self.assertIn('CLAIM= PAIR=1 IDS=[] TOKEN=set', calls[0])
        self.assertIn('--auto', calls[0])
        self.assertTrue(calls[1].endswith('--pair --lang=ru'), calls[1])
        flat = ' '.join(self.out.split())
        self.assertIn('Напишу боту и подтвержу здесь, в терминале', flat)
        self.assertIn('После установки откроется окно привязки', flat)
        self.assertIn('Бот установлен и запущен — осталось привязать администратора.', flat)

    def test_no_one_time_code_and_no_bot_queue_in_the_terminal(self):
        # Код — путь без терминала; мастер очередь бота не читает: ни getUpdates, ни чужих имён на экране.
        self.assertNotIn('CLAIM=1', self._read(self.bot_log))
        self.assertNotIn('getUpdates', self._read(self.stdin_log) + self._read(self.argv_log))
        self.assertNotIn('777000111', self.out)

    def test_token_never_reaches_the_command_line(self):
        log = self._read(self.bot_log)
        self.assertTrue(log, self.out[-3000:])
        for line in log.splitlines():
            self.assertNotIn(FAKE_BOT_TOKEN, line.split('ARGV=', 1)[-1], 'токен в аргументах виден в /proc/*/cmdline')
        self.assertNotIn(FAKE_BOT_TOKEN, self._read(self.argv_log))

    def test_menu_item_opens_the_same_binding_window(self):
        # 6 — Telegram-бот, 6 — Администраторы, 1 — Привязать по сообщению, Enter — пауза, 0 — назад, 0 — выход
        before = self._read(self.bot_log).count('--pair')
        out = self.stand.keys(['6\n', '6\n', '1\n', '\n', '0\n', '0\n'])
        self.assertEqual(self._read(self.bot_log).count('--pair'), before + 1, out[-3000:])
        flat = ' '.join(out.split())
        self.assertIn('6) Администраторы', flat)
        self.assertIn('7) Удалить бота', flat)
        self.assertIn('1) Привязать по сообщению', flat)
        self.assertIn('Администраторов нет — бот никого не пускает.', flat)


class BotTokenQuestionHasAWayOut(unittest.TestCase):
    """1d.1 №7: у вопроса про токен бота обязан быть выход.

    Цикл `while true` не знал ни отмены, ни счётчика попыток, ни конца ввода:
    человек, у которого токена под рукой нет, выходил из мастера только
    Ctrl+C, а на оборванной сессии установщик крутился вхолостую. Главное меню
    от этого защищено давно — там разбор EOF описан у `read -t 30`.
    """

    ANSWERS = ['4\n', '\n', '\n', 'y\n']      # 4 Lite → профили → порт → «ставим бота»

    def test_zero_cancels_and_the_wizard_goes_on(self):
        stand = Stand()
        try:
            out = stand.keys(self.ANSWERS + ['0\n', '\n', '0\n'])
            self.assertIn('Токен бота от @BotFather (ввод скрыт, 0 — отмена)', out)
            self.assertIn('Отменено', out)
            # Ноль — это отмена бота, а не обрыв мастера: установка доведена.
            self.assertTrue(stand.config().get('installed'), out[-3000:])
            self.assertFalse((stand.tmp / 'bot' / '.env').exists(), out[-3000:])
        finally:
            stand.close()

    def test_three_misses_give_up_instead_of_asking_forever(self):
        stand = Stand()
        try:
            out = stand.keys(self.ANSWERS + ['nope\n', 'nope\n', 'nope\n', '\n', '0\n'])
            self.assertEqual(out.count('Не похоже на токен бота'), 3, out[-4000:])
            self.assertIn('Токен так и не принят', out)
            self.assertTrue(stand.config().get('installed'), out[-3000:])
        finally:
            stand.close()

    def test_hidden_input_is_explained_once_and_a_good_token_is_acknowledged(self):
        # NC-24: ввод скрыт, и после вставки на экране ничего не меняется — заранее говорим, что так задумано, а
        # принятый токен подтверждаем строкой. «0» на выборе администратора — отмена бота, мастер идёт дальше.
        stand = Stand()
        try:
            out = stand.keys(self.ANSWERS + ['nope\n', '123456789:' + 'A' * 35 + '\n', '0\n', '\n', '0\n'])
            flat = ' '.join(out.split())
            self.assertEqual(flat.count('на экране не появится — так задумано'), 1, out[-3000:])
            self.assertLess(flat.find('так задумано'), flat.find('Токен бота от @BotFather'), out[-3000:])
            self.assertIn('Токен принят.', out, out[-3000:])
            self.assertTrue(stand.config().get('installed'), out[-3000:])
        finally:
            stand.close()

    def test_end_of_input_does_not_spin(self):
        """Оборванная сессия: ответы кончились прямо на вопросе про токен.

        Идём через МЕНЮ, а не через мастер: вопрос мастера про бота задаёт
        confirm, а он читает управляющий терминал, которого у оборванной
        сессии нет, и сам отвечает «нет». Меню читает stdin, поэтому пункт
        «Telegram-бот → Установить» доводит до того самого вопроса, за которым
        сразу наступает конец ввода.
        """
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.pipe_answers(['6\n', '1\n'], seconds=120)
            # Спросили один раз — и вышли, а не спрашивают вечно на 100 % CPU.
            self.assertEqual(out.count('Токен бота от @BotFather'), 1, out[-4000:])
            self.assertEqual(out.count('Не похоже на токен бота'), 0, out[-4000:])
            self.assertFalse((stand.tmp / 'bot' / '.env').exists(), out[-3000:])
        finally:
            stand.close()


class BackFromSectionsWithoutPause(unittest.TestCase):
    """LC-06: «0 — назад» из раздела главного меню возвращает сразу, без лишнего «Нажмите Enter...».

    И SR-53: главный экран читает первый символ отдельно — набранная цифра не теряется при перерисовке.
    """

    def test_zero_from_users_and_protocols_returns_straight_to_the_menu(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(['1\n', '0\n', '3\n', '0\n', '0\n'])
        finally:
            stand.close()
        self.assertIn('Пользователи', out, out[-3000:])
        self.assertNotIn('Нажмите Enter', out, out[-3000:])
        # «0» в главном меню — выход: до него дошли без рассинхронизации клавиш.
        self.assertIn('До встречи', out, out[-2000:])


class BrokenUsersFileInTheList(unittest.TestCase):
    """1d.1 №11: битый users.json — это не «пользователей пока нет».

    Подстановка команды съедала флаг GA_USERS_BROKEN, и экран списка рисовал
    «Пользователей пока нет». Человек читает это как «сервер пуст» и заводит
    людей заново — поверх существующих ключей. В счётчике карточки тот же узор
    обойдён давно через ga_capture.
    """

    def test_list_says_the_file_is_damaged_not_that_there_are_no_users(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            users = stand.dir / 'users.json'
            self.assertTrue(users.exists())
            # Обрыв записи: разобранная часть есть, хвоста нет.
            users.write_text(users.read_text(encoding='utf-8')[:30], encoding='utf-8')
            out = stand.keys(['1\n', '0\n', '0\n'])
            self.assertIn('Файл пользователей повреждён', out, out[-3000:])
            self.assertNotIn('Пользователей пока нет', out, out[-3000:])
            # И карточка на дашборде говорит то же самое, а не «0».
            self.assertIn('файл сломан', out, out[-3000:])
        finally:
            stand.close()


class KeyHandover(unittest.TestCase):
    """§1d.2 №2-3: выбор профиля при выдаче ключа и «завести → сразу отдать».

    Выбор профиля был единственным вопросом во всей цепочке ключей на голом
    read: ни «0) Назад», ни переспроса, а на любой промах — «У пользователя нет
    ни одного профиля», хотя экран только что показал два. А после заведения
    человека ключ не предлагался вовсе: «создан» и возврат в список.
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)     # admin с профилями awg2 и awg3

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_profile_pick_has_a_title_line_names_and_a_way_back(self):
        # Пользователи → первая строка → 1 (Ключ) → 3 (ссылка vpn://) → 0 на выборе профиля
        out = self.stand.keys(['1\n', '1\n', '1\n', '3\n', '0\n', '0\n', '0\n', '0\n'])
        self.assertIn('Каким профилем выдать ключ для admin?', out, out[-3000:])
        # NC-27: 3.1 первым — как совет «берите 3.1» и страница; список людей и карточка — в том же порядке
        # Профили — подписями с портом (решение владельца 15.10), колонка «Профили» списка — линиями
        ports = {p: v['port'] for p, v in self.stand.config()['profiles'].items()}
        self.assertRegex(out, r'1\) AmneziaWG 3\.1 · порт %d\n\s+2\) AmneziaWG 2\.0 · порт %d\n\s+0\) « Назад'
                         % (ports['awg3'], ports['awg2']))
        self.assertRegex(out, r'1\) ● admin\s+Администратор\s+3\.1 2\.0\s')
        self.assertIn('Профили: AmneziaWG 3.1 · порт %d, AmneziaWG 2.0 · порт %d' % (ports['awg3'], ports['awg2']), out)
        # Имена линеек, а не голые awg2/awg3
        self.assertNotRegex(out.split('Каким профилем')[-1].split('0) « Назад')[0], r'awg[123]')
        self.assertIn('0) « Назад', out)
        # «Назад» — это отмена, а не ложное «нет ни одного профиля»
        self.assertNotIn('нет ни одного профиля', out.split('Каким профилем')[-1])

    def test_wrong_answer_is_asked_again_not_called_a_missing_profile(self):
        out = self.stand.keys(['1\n', '1\n', '1\n', '3\n', '9\n', '1\n',
                               '\n', '0\n', '0\n', '0\n', '0\n'])
        tail = out.split('Каким профилем')[-1]
        self.assertNotIn('нет ни одного профиля', tail, tail[:2000])
        # После промаха ключ всё-таки выдан
        self.assertIn('vpn://', tail, tail[:3000])

    def test_new_user_is_offered_a_key_right_away(self):
        # «Добавить» — номер сразу за списком; в этом стенде людей двое (admin и
        # заведённый соседней проверкой), поэтому берём его из легенды экрана.
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'nina\n', '\n', '\n', '\n',
                               '0\n', '\n', '0\n', '0\n'])
        self.assertIn('Показать ключ для nina прямо сейчас?', out, out[-4000:])

    def test_the_list_jumps_to_the_page_with_the_new_user(self):
        # Страницу делаем маленькой: иначе для проверки нужен 21 человек.
        env_before = self.stand.env.get('GOAMNEZIA_USERS_PAGE_SIZE')
        self.stand.env['GOAMNEZIA_USERS_PAGE_SIZE'] = '1'
        try:
            out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'olga\n', '\n', '\n', '\n',
                                   '0\n', '\n', '0\n', '0\n'])
        finally:
            if env_before is None:
                self.stand.env.pop('GOAMNEZIA_USERS_PAGE_SIZE', None)
            else:
                self.stand.env['GOAMNEZIA_USERS_PAGE_SIZE'] = env_before
        # Список после возврата обязан показывать СТРОКУ olga, а не первую
        # страницу: при размере страницы в одного человека это видно сразу.
        listing = out.split('Номер строки — открыть карточку')[-2]
        self.assertRegex(listing, r'\d+\)\s+[●○⏳▮·]\s+olga', listing[-1500:])


class DisabledUserKey(unittest.TestCase):
    """§1d.2 №1: ключ выключенному выдаётся, но экран об этом говорит."""

    def test_screen_says_the_person_is_disabled(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # admin → 5 (выключить) → 1 (ключи) → 3 (ссылка vpn://) → профиль 1
            out = stand.keys(['1\n', '1\n', '5\n', '\n', '1\n', '3\n', '1\n',
                              '\n', '0\n', '0\n', '0\n', '0\n'])
            self.assertIn('admin сейчас выключен', out, out[-4000:])
            self.assertIn('vpn://', out, out[-4000:])
        finally:
            stand.close()


class SharesScreen(unittest.TestCase):
    """§1d.2 №4: раздачу из SSH можно было создать и нечем снять."""

    def test_users_screen_has_a_shares_item_that_opens_and_returns(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            share = user_items(stand)[2]
            out = stand.keys(['1\n', '%d\n' % share, '0\n', '0\n', '0\n'])
            # Пункт должен БЫТЬ на экране, а не только отзываться на нажатие:
            # человек не угадывает номера, он читает их с экрана.
            listing = out.split('Номер строки — открыть карточку')[0]
            self.assertIn('%d) Активные раздачи' % share, listing, listing[-2000:])
            self.assertIn('Активные раздачи', out, out[-3000:])
            self.assertIn('Сейчас ничего не раздаётся', out, out[-3000:])
            self.assertIn('Снять все раздачи', out, out[-3000:])
        finally:
            stand.close()


class SubsScreen(unittest.TestCase):
    """Карточка пользователя → «Подписка» (docs/SUBSCRIPTION.md).

    Стенд — Lite без домена: подписки нет, создание честно отвечает, что ей негде жить, до любых вопросов и без
    единого адреса — и называет, чем отдать ключ без неё."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_card_item_and_lite_refusal(self):
        # 1 — пользователи, 1 — admin, 8 — подписка, 1 — создать, Enter — пауза, 0 — назад до выхода
        out = self.stand.keys(['1\n', '1\n', '8\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n'])
        # Подпись пункта — «Подписка», без «(Clash, роутеры)»: в подписке и приложение Amnezia, и роутер, и incy
        self.assertIn('8) Подписка', out, out[-2500:])
        self.assertNotIn('Подписка (Clash', out)
        if 'нет функции subs_list' in out:
            self.skipTest('ядро подписок не подключено')
        flat = ' '.join(out.split())
        self.assertIn('Подписка: нет', flat, out[-3000:])
        self.assertIn('1) Создать подписку', out, out[-3000:])
        self.assertIn('Подписка работает только на сайте с доменом и настоящим сертификатом, а у этого сервера его '
                      'сейчас нет.', flat, out[-3000:])
        self.assertIn('временная ссылка на файл или страница подключения', flat)
        for leak in ('/s-', 'mihomo.yaml', 'sub-create', '--name='):
            self.assertNotIn(leak, out)


class SubsClientsScreen(unittest.TestCase):
    """«Показать адреса» подписки с выбором по клиенту (поле clients, фикстура tests/fixtures/sub_clients.json).

    Поле clients в ядре делает другой исполнитель (lib/subs.sh), поэтому экран зовётся прямо на фикстуре: install.sh
    без последней строки (main) подключается в bash, ответ ядра — фикстура. Записи с одним app — один пункт списка.
    Номер приложения — его экран без адреса (решение владельца 04.10): 1) QR-код (по qr и qr_alt; qr "" — с причиной),
    2) показать, что вставлять (по copy: адрес, команда, ключ линии), «другой формат» и «другая линия»; QR и ключ линии —
    тем же путём, что «Ключ: QR» и «Ключ: ссылка» (_user_key; в аудит — выдача ключа). «Короткая ссылка» — только с
    short_available (SubsShortScreen). Адрес не идёт в argv jq и qrencode (SEC46-02). Без clients (ядро старше) — прежний
    список по форматам."""

    FIXTURE = ROOT / 'tests' / 'fixtures' / 'sub_clients.json'
    SECRET = 'FAKESUBSECRETFAKESUBSECRETFAKESUBSECRET0123'

    def run_screen(self, keys, patch=None):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-subc-'))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        menu = tmp / 'menu'
        menu.mkdir()
        (menu / 'lib').symlink_to(ROOT / 'lib')
        src = (ROOT / 'install.sh').read_text(encoding='utf-8')
        (menu / 'install.sh').write_text(src.rstrip('\n').rsplit('\n', 1)[0] + '\n', encoding='utf-8')  # без main "$@"
        answer = json.loads(self.FIXTURE.read_text(encoding='utf-8'))
        if patch:
            patch(answer)
        (tmp / 'answer.json').write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')
        # Шпионы: аргументы jq и qrencode — в файл, дальше — настоящая команда
        spy = tmp / 'spy'
        spy.mkdir()
        argv_log = tmp / 'argv.log'
        for tool in ('jq', 'qrencode'):
            real = shutil.which(tool, path=SYSTEM_PATH)
            if real:
                (spy / tool).write_text('#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' % (argv_log, real),
                                        encoding='utf-8')
                (spy / tool).chmod(0o755)
                require_exec(spy / tool)
        gdir = tmp / 'goamnezia'
        gdir.mkdir()
        (gdir / '.language').write_text('ru', encoding='utf-8')
        # Профили, по которым выдаётся ключ, стоят — как на настоящем сервере: заголовок ключа называет профиль
        # подписью с портом (решение владельца 15.10), а не «— не установлена».
        (gdir / 'config.json').write_text(json.dumps({'profiles': {
            'awg3': {'installed_at': '2026-10-15T00:00:00Z', 'line': '3.1', 'port': 6714},
            'awg2': {'installed_at': '2026-10-15T00:00:00Z', 'line': '2.0', 'port': 5807}}}), encoding='utf-8')
        (tmp / 'shm').mkdir(mode=0o700)
        env = dict(os.environ, GOAMNEZIA_DRY_RUN='1', GOAMNEZIA_DIR=str(gdir), GOAMNEZIA_LOCK_DIR=str(tmp / 'run'),
                   GOAMNEZIA_LOG_FILE=str(tmp / 'goamnezia.log'), GOAMNEZIA_AUDIT_LOG=str(tmp / 'audit.log'),
                   GOAMNEZIA_SECRET_TMPDIR=str(tmp / 'shm'), COLUMNS='200', LINES='120', TERM='dumb',
                   PATH=str(spy) + os.pathsep + SYSTEM_PATH)
        # Ключ человека — заглушкой: экран проверяет путь выдачи, а не сборку ключа
        script = ('source "$1"\n'
                  'export_vpn() { printf "vpn://FAKEKEY-%s-%s" "$1" "$2"; }\n'
                  'export_conf() { printf "[Interface]\\nPrivateKey = FAKEPRIV-%s-%s\\n" "$1" "$2"; }\n'
                  '_subs_show_urls "$(cat "$2")"\n'
                  'echo "GA_BACK=$GA_BACK"\n')
        proc = subprocess.run([BASH, '-c', script, '_', str(menu / 'install.sh'), str(tmp / 'answer.json')],
                              input=''.join(keys), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              timeout=120, env=env)
        audit = (tmp / 'audit.log').read_text(encoding='utf-8') if (tmp / 'audit.log').exists() else ''
        argv = argv_log.read_text(encoding='utf-8') if argv_log.exists() else ''
        return clean(proc.stdout), audit, argv

    def url(self, suffix):
        return 'https://vpn.example.com/s-%s/%s' % (self.SECRET, suffix)

    def test_choice_by_app_and_each_app_screen(self):
        # 3 — Clash Verge Rev: 1 (QR не нужен), Enter, 2 (что вставлять), Enter, 0; 7 — AmneziaVPN: 1 (QR ключа 3.1),
        # Enter, 3 (другая линия), 2 (ключ 2.0), Enter, 0; 10 — Keenetic: 2 (команда), Enter, 0; 0 — назад
        out, audit, argv = self.run_screen(['3\n', '1\n', '\n', '2\n', '\n', '0\n', '7\n', '1\n', '\n', '3\n', '2\n', '\n', '0\n',
                                            '10\n', '2\n', '\n', '0\n', '0\n'])
        flat = ' '.join(out.split())
        listing = out.split('Выбор:')[0]
        self.assertIn('Подписка — Анна: приложения', listing, listing)
        self.assertLess(listing.index('Обновляется сам — приложение само забирает свежий ключ'), listing.index('1) FlClash'))
        self.assertLess(listing.index('6) AWG Manager'), listing.index('Разово — ключ добавляется по ссылке один раз'))
        self.assertIn('3) Clash Verge Rev · рекомендуем  Windows, macOS, Linux', listing)
        self.assertEqual(listing.count('AWG Manager'), 1, 'записи с одним app — один пункт')
        self.assertIn('Apple TV', listing)
        self.assertIn('10) Keenetic / Netcraze  Роутер', listing)
        self.assertIn('12) goVLESS, Kaskad, goTelegram', listing)
        self.assertNotIn('/s-', listing, 'в списке приложений адресов нет')
        # Прежнего списка по форматам нет
        self.assertNotIn('Mihomo Party, Clash Meta for Android — профиль mihomo', out)
        # Clash Verge Rev: экран без адреса — QR с причиной и «показать, что вставлять»; адрес — только по пункту 2
        verge = out.split('Clash Verge Rev — подписка Анна')[1]
        self.assertNotIn('/s-', verge.split('Выбор:')[0], 'на экране приложения адреса нет')
        self.assertIn('Обновляется сам — раз в 12 ч · Windows, macOS, Linux', ' '.join(verge.split()))
        self.assertIn('1) QR-код — на компьютере не нужен', verge)
        self.assertIn('2) Показать, что вставлять — ссылку', verge)
        self.assertIn('Вставьте в Clash Verge Rev — «Профили»: вставьте ссылку в поле «URL профиля»', flat)
        self.assertIn('    ' + self.url('all.yaml'), out)
        self.assertIn('Кто знает адрес — получает ключи этого пользователя', flat)
        self.assertNotIn('Короткая ссылка', out)
        # AmneziaVPN: QR ключа и ключ vpn:// — той же выдачей, что «Ключ: QR» и «Ключ: ссылка», по линии
        self.assertIn('1) QR-код\n', out)
        self.assertIn('2) Показать, что вставлять — ключ vpn://', out)
        self.assertIn('3) Другая линия — сейчас AmneziaWG 3.1', out)
        self.assertIn('Ключ приложения Amnezia: anna, профиль AmneziaWG 3.1 · порт 6714\n', out)
        self.assertIn('Ключ приложения Amnezia: anna, профиль AmneziaWG 2.0 · порт 5807\n', out)
        self.assertIn('Линия: AmneziaWG 2.0', out)
        self.assertIn('vpn://FAKEKEY-anna-awg2', out)
        self.assertIn('anna profile=awg3 format=qr-vpn', audit)
        self.assertIn('anna profile=awg2 format=vpn', audit)
        # Keenetic: команда целиком, QR не нужен
        keenetic = out.split('Keenetic / Netcraze — подписка Анна')[1]
        self.assertIn('1) QR-код — роутеру не нужен', keenetic)
        self.assertIn('2) Показать, что вставлять — команду', keenetic)
        self.assertIn('    interface Wireguard9 wireguard import ' + self.url('awg20.conf'), keenetic)
        self.assertTrue(out.rstrip().endswith('GA_BACK=1'), out[-300:])
        # SEC46-02: адрес — только через stdin, не аргументом jq и qrencode
        self.assertGreater(len(argv.splitlines()), 10, 'шпион jq не сработал')
        self.assertNotIn(self.SECRET, argv)

    def test_two_qr_codes_and_one_app_with_formats(self):
        # 5 — incy: 1 (два QR), Enter, 0; 6 — AWG Manager: 3, 3 (формат .conf), 4 (линия 2.0), 2 (текст .conf), Enter, 0; 0
        out, audit, argv = self.run_screen(['5\n', '1\n', '\n', '0\n', '6\n', '3\n', '3\n', '4\n', '2\n', '\n', '0\n', '0\n'])
        incy = out.split('incy — подписка Анна')[1]
        self.assertIn('1) QR-код\n', incy)
        if shutil.which('qrencode', path=SYSTEM_PATH):
            self.assertLess(incy.index('Камерой телефона: наведите обычную камеру — откроется incy:'),
                            incy.index('Сканером в incy: наведите сканер приложения на этот код:'))
            self.assertIn('█', incy)
        awgm = out.split('AWG Manager — подписка Анна')
        self.assertIn('Формат: Все линии .txt', awgm[1])
        self.assertIn('1) QR-код — роутеру не нужен', awgm[1])
        self.assertIn('3) Другой формат — сейчас «Все линии .txt»', awgm[1])
        self.assertNotIn('Другая линия', awgm[1].split('Выбор:')[0], 'у «Все линии .txt» одна ссылка')
        self.assertIn('Формат: Ключ vpn://', awgm[2])
        self.assertIn('Формат: Файл .conf', awgm[3])
        self.assertIn('Линия: AmneziaWG 2.0', awgm[4])
        self.assertIn('2) Показать, что вставлять — текст .conf', awgm[4])
        self.assertIn('PrivateKey = FAKEPRIV-anna-awg2', out)
        self.assertIn('anna profile=awg2 format=conf', audit)
        self.assertNotIn(self.SECRET, argv)

    def test_foreign_scheme_app_link_and_short_link_are_not_printed(self):
        def patch(r):
            for c in r['clients']:
                if c['id'] == 'incy':
                    c['items'][0]['deeplink'] = 'javascript://%0Aalert(1)'
                if c['id'] == 'flclash':
                    c['items'][0]['short_url'] = 'https://vpn.example.com/K7Q-2MXP'
        out, _, _ = self.run_screen(['5\n', '1\n', '\n', '0\n', '1\n', '0\n', '0\n'], patch)
        self.assertIn('incy — подписка Анна', out)
        self.assertIn('1) QR-код — не для этого приложения', out)
        self.assertNotIn('javascript', out)
        # «Короткая ссылка» — только с short_available (бот настроен): без него пункта нет даже с short_url
        self.assertNotIn('K7Q-2MXP', out)
        self.assertNotIn('Короткая ссылка', out)

    def test_without_clients_the_old_list_by_format(self):
        def patch(r):
            del r['clients']
            r['urls'][0] = {'suffix': 'mihomo.yaml', 'format': 'mihomo', 'line': '', 'url': self.url('mihomo.yaml')}
        out, _, _ = self.run_screen(['\n'], patch)
        flat = ' '.join(out.split())
        self.assertIn('Clash Verge Rev, FlClash, Mihomo Party, Clash Meta for Android — профиль mihomo', flat)
        self.assertIn(self.url('mihomo.yaml'), out)
        self.assertIn('AmneziaWG 2.0: ' + self.url('awg20.conf'), out)
        self.assertNotIn('Подписка — Анна: приложения', out)


class SubsShortScreen(unittest.TestCase):
    """Короткие ссылки с 2FA через бота в SSH — в настоящем pty (фикстура tests/fixtures/short_links.json, договор ядра и
    интерфейсов). Экран приложения подписки: «Короткая ссылка» (short-ensure: код крупно, адрес, роутеру — команда с
    короткой ссылкой, пометка про 2FA) и «Заменить короткую ссылку» (short-replace-request с who=ssh: число на экран, опрос
    short-status до итога); экран подписки: баны со снятием (short-bans, short-unban; код ••••XXXX). Действия ядра делает
    другой исполнитель, поэтому мост в той же оболочке (action_dispatch) — заглушкой в форме договора, каталог — копия
    настоящего плюс short-* фикстуры. Без short_available пунктов нет."""

    SUB_FIXTURE = ROOT / 'tests' / 'fixtures' / 'sub_clients.json'
    SHORT_FIXTURE = ROOT / 'tests' / 'fixtures' / 'short_links.json'

    FAKE_BRIDGE = r'''
action_dispatch() {
    local a name=""
    for a in "$@"; do case "$a" in --action=*) name="${a#--action=}" ;; esac; done
    printf '%s who=%s\n' "$*" "${GOAMNEZIA_WHO:-}" >> "$GA_T_ARGS"
    echo "шум моста до JSON"
    python3 - "$GA_T_FX" "$name" "$GA_T_CNT" <<'PY'
import json, os, sys, time
fx = json.load(open(sys.argv[1], encoding="utf-8"))
name, cnt = sys.argv[2], sys.argv[3]
fail = os.environ.get("GA_T_FAIL_" + name.replace("-", "_").upper())
if fail:
    print(fail)          # отказ, как его отдаёт мост: слова ядра и код (SL-UX-03)
    sys.exit(0)
r = dict(fx["actions"][name])
soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 300))
if name == "short-replace-request":
    r["expires"] = soon
if name == "short-status":
    n = int(open(cnt).read()) + 1 if os.path.exists(cnt) else 1
    open(cnt, "w").write(str(n))
    final = n >= 2 and not os.environ.get("GA_T_PENDING")
    r = {"status": "ok", "display": "Q8RT-W2KM", "url": "https://vpn.example.com/Q8RT-W2KM"} if final else {"status": "pending", "expires": soon}
print(json.dumps(r, ensure_ascii=False))
PY
}
'''

    @staticmethod
    def pty_keys(argv, env, keys, seconds=120):
        """Как Stand.keys: команда в pty, клавиша — когда экран замолчал; («until», текст) — дождаться текста."""
        master, slave = pty.openpty()
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(ROOT), close_fds=True,
                                preexec_fn=_session_with_tty)
        os.close(slave)
        out, queue, quiet, idle = b'', list(keys), 0, 0
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.4)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                quiet = idle = 0
                continue
            if queue:
                quiet += 1
                if quiet < 2:
                    continue
                item = queue.pop(0)
                if isinstance(item, tuple) and item[0] == 'until':
                    if item[1] not in clean(out.decode('utf-8', 'replace')):
                        queue.insert(0, item)
                        continue
                else:
                    os.write(master, item.encode())
                quiet = idle = 0
                continue
            if proc.poll() is not None:
                break
            idle += 1
            if idle >= 4:
                break
        try:
            os.killpg(proc.pid, 9)
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        os.close(master)
        return clean(out.decode('utf-8', 'replace'))

    def run_short(self, keys, short=True, then='', poll='0.3', pending=False, env_extra=None):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-short-'))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        menu = tmp / 'menu'
        menu.mkdir()
        (menu / 'lib').symlink_to(ROOT / 'lib')
        src = (ROOT / 'install.sh').read_text(encoding='utf-8')
        (menu / 'install.sh').write_text(src.rstrip('\n').rsplit('\n', 1)[0] + '\n', encoding='utf-8')  # без main "$@"
        answer = json.loads(self.SUB_FIXTURE.read_text(encoding='utf-8'))
        if short:
            answer['short_available'] = True
        (tmp / 'answer.json').write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')
        fx = json.loads(self.SHORT_FIXTURE.read_text(encoding='utf-8'))
        catalog = json.loads((ROOT / 'lib' / 'actions.json').read_text(encoding='utf-8'))
        catalog['actions'].update(fx['catalog']['actions'])
        catalog['param_types'].update(fx['catalog']['param_types'])
        (tmp / 'actions.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        # Шпион jq: адрес и код — только через stdin, не аргументом (SEC46-02)
        spy = tmp / 'spy'
        spy.mkdir()
        argv_log = tmp / 'argv.log'
        real = shutil.which('jq', path=SYSTEM_PATH)
        (spy / 'jq').write_text('#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' % (argv_log, real), encoding='utf-8')
        (spy / 'jq').chmod(0o755)
        require_exec(spy / 'jq')
        gdir = tmp / 'goamnezia'
        gdir.mkdir()
        (gdir / '.language').write_text('ru', encoding='utf-8')
        (tmp / 'shm').mkdir(mode=0o700)
        env = dict(os.environ, GOAMNEZIA_DRY_RUN='1', GOAMNEZIA_DIR=str(gdir), GOAMNEZIA_LOCK_DIR=str(tmp / 'run'),
                   GOAMNEZIA_LOG_FILE=str(tmp / 'goamnezia.log'), GOAMNEZIA_AUDIT_LOG=str(tmp / 'audit.log'),
                   GOAMNEZIA_SECRET_TMPDIR=str(tmp / 'shm'), GOAMNEZIA_ACTIONS_JSON=str(tmp / 'actions.json'),
                   GA_T_ARGS=str(tmp / 'bridge.log'), GA_T_FX=str(self.SHORT_FIXTURE), GA_T_CNT=str(tmp / 'status.cnt'),
                   GA_SHORT_POLL=poll, COLUMNS='200', LINES='120', TERM='dumb', PATH=str(spy) + os.pathsep + SYSTEM_PATH)
        for var in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY', 'GA_T_PENDING'):
            env.pop(var, None)
        if pending:
            env['GA_T_PENDING'] = '1'      # ядро так и не решило: ждём, пока человек не нажмёт Enter
        env.update(env_extra or {})
        script = ('source "$1"\n' + self.FAKE_BRIDGE +
                  'export_vpn() { printf "vpn://FAKEKEY-%s-%s" "$1" "$2"; }\n'
                  '_subs_show_urls "$(cat "$2")"\n' + then + 'echo "ВСЁ"\n')
        out = self.pty_keys([BASH, '-c', script, '_', str(menu / 'install.sh'), str(tmp / 'answer.json')], env, keys)
        bridge = (tmp / 'bridge.log').read_text(encoding='utf-8') if (tmp / 'bridge.log').exists() else ''
        argv = argv_log.read_text(encoding='utf-8') if argv_log.exists() else ''
        return out, bridge, argv

    def test_short_link_swap_and_bans_on_a_pty(self):
        # 1 — FlClash: 3 (короткая ссылка), Enter, 4 (замена: число, опрос до итога), Enter, 0; 10 — Keenetic: 3, Enter,
        # 0; 0 — из приложений; затем баны: 2 (снять заморозку кода), Enter, 0
        keys = ['1\n', '3\n', '\n', '4\n', ('until', 'Ссылка заменена'), '\n', '0\n', '10\n', '3\n', '\n', '0\n', '0\n',
                ('until', 'Баны коротких ссылок'), '2\n', '\n', '0\n']
        out, bridge, argv = self.run_short(keys, then='_subs_short_bans\n')
        self.assertTrue(out.rstrip().endswith('ВСЁ'), out[-1500:])
        flc = out.split('FlClash — подписка Анна')[1]
        self.assertIn('3) Короткая ссылка — набрать руками, 2FA в Telegram', flc)
        self.assertIn('4) Заменить короткую ссылку — подтверждение в Telegram-боте', flc)
        flat = ' '.join(out.split())
        # Показ: код крупно и адрес, пометка про 2FA, как набирать
        self.assertIn('Короткая ссылка — FlClash · Все линии', flat)
        self.assertIn('NV43-9BKX', out)
        self.assertIn('https://vpn.example.com/NV43-9BKX', out)
        self.assertIn('Короткие ссылки работают с 2FA через Telegram-бота', flat)
        self.assertIn('O читается как 0', flat)
        self.assertIn('--action=short-ensure --json --name=anna --suffix=all.yaml who=ssh', bridge)
        # Замена: число на экран, опрос short-status до итога, новый код
        self.assertIn('--action=short-replace-request --json --name=anna --suffix=all.yaml --who=ssh', bridge)
        self.assertIn('Число на экране: 47', flat)
        self.assertRegex(flat, r'Ждём подтверждения · [45]:[0-5][0-9]')
        self.assertEqual(bridge.count('--action=short-status --json --request=rq7Kq2mXa'), 2)
        self.assertIn('Ссылка заменена — прежняя больше не работает. Новая:', flat)
        self.assertIn('Q8RT-W2KM', out)
        # Роутеру — команда с короткой ссылкой
        self.assertIn('Команда для роутера с короткой ссылкой:', out)
        self.assertIn('interface Wireguard9 wireguard import https://vpn.example.com/NV43-9BKX', out)
        self.assertIn('--action=short-ensure --json --name=anna --suffix=awg20.conf', bridge)
        # Баны: код ••••XXXX, снятие — целью целиком мосту, на экран код не идёт
        bans = out.split('Баны коротких ссылок')[1]
        self.assertIn('1) IP 203.0.113.7', bans)
        self.assertIn('2) код ••••MXPA', bans)
        self.assertIn('подбор: три неверных кода подряд · бан на сутки (первый раз)', ' '.join(bans.split()))
        self.assertIn('три неверных числа подряд · заморозка на месяц (повторно)', ' '.join(bans.split()))
        self.assertNotIn('ступень', bans)
        self.assertNotIn('K7Q2MXPA', out)
        self.assertIn('--action=short-unban --json --target=K7Q2MXPA who=ssh', bridge)
        self.assertIn('Снято: код ••••MXPA', flat)
        # SEC46-02: код и адрес — только через stdin jq
        self.assertGreater(len(argv.splitlines()), 10, 'шпион jq не сработал')
        for secret in ('NV43-9BKX', 'Q8RT-W2KM', 'K7Q2MXPA', 'FAKESUBSECRET'):
            self.assertNotIn(secret, argv)

    def test_enter_stops_waiting_and_no_items_without_2fa(self):
        # Замена: Enter сразу — ждать перестали, запрос остаётся в боте
        # Опрос раз в 1,5 с — между строками ожидания экран молчит, и Enter доходит до вопроса
        out, bridge, _ = self.run_short(['1\n', '4\n', ('until', 'Ждём подтверждения'), '\n', '\n', '0\n', '0\n'],
                                        poll='1.5', pending=True)
        self.assertIn('Ждать перестали: запрос остаётся в боте до своего срока.', ' '.join(out.split()))
        self.assertLessEqual(bridge.count('short-status'), 2)
        self.assertNotIn('Ссылка заменена', out)
        # Без short_available (бот не настроен) — пунктов нет, нумерация прежняя
        out, bridge, _ = self.run_short(['1\n', '0\n', '0\n'], short=False)
        flc = out.split('FlClash — подписка Анна')[1]
        self.assertNotIn('Короткая ссылка', flc)
        self.assertNotIn('Заменить короткую ссылку', flc)
        self.assertEqual(bridge, '', 'мост коротких ссылок не звали')

    def test_short_refusals_are_words_without_the_service_code(self):
        """SL-UX-03 (настоящий бот на 168: «… попробуйте через 5 мин (short_pending)»): отказ short-* в SSH — словами ядра
        (они говорят, что делать), без кода в скобках; слов нет или отказ незнакомый — «не выполнено» и «повторите…»."""
        words = 'По этой короткой ссылке уже ждут подтверждения в боте — попробуйте через 5 мин'
        fail = lambda code, message: json.dumps({'status': 'error', 'message': message, 'code': code}, ensure_ascii=False)  # noqa: E731
        extra = {'GA_T_FAIL_SHORT_REPLACE_REQUEST': fail('short_pending', words),
                 'GA_T_FAIL_SHORT_ENSURE': fail('short_failed', 'Действие не выполнено (short_failed).'),
                 'GA_T_FAIL_SHORT_BANS': fail('write_failed', '')}
        # 1 — FlClash: 4 (замена — отказ ядра), Enter, 3 (короткая ссылка — незнакомый отказ), Enter, 0, 0; баны — отказ
        out, bridge, _ = self.run_short(['1\n', '4\n', ('until', 'попробуйте через 5 мин'), '\n', '3\n',
                                         ('until', 'Действие не выполнено'), '\n', '0\n', '0\n'],
                                        then='_subs_short_bans || true\n', env_extra=extra)
        self.assertTrue(out.rstrip().endswith('ВСЁ'), out[-1500:])
        self.assertIn('--action=short-replace-request', bridge)
        self.assertIn('--action=short-ensure', bridge)
        self.assertIn('--action=short-bans', bridge)
        flat = ' '.join(out.split())
        self.assertIsNone(re.search(r'\([A-Za-z0-9_.:=-]+\)', out), 'служебный код в скобках: %s' % flat[-600:])
        for code in ('short_pending', 'short_failed', 'write_failed'):
            self.assertNotIn(code, out)
        self.assertIn(words, flat)
        self.assertIn('Действие не выполнено Повторите через минуту; не выйдет снова — посмотрите «Состояние → Логи».', flat)
        self.assertIn('Короткие ссылки: действие не выполнено. Повторите через минуту', flat)


class PagesScreen(unittest.TestCase):
    """Карточка пользователя → «Страница подключения» (docs/CLIENTS.md §10).

    Стенд — Lite без домена: список пуст, а создание честно отвечает, что
    странице негде жить (pages_need_domain), и предлагает прежний путь —
    временную ссылку на файл. Без модуля экран говорит «модуль не подключён».
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_card_item_opens_the_list_and_returns(self):
        out = self.stand.keys(['1\n', '1\n', '7\n', '0\n', '0\n', '0\n', '0\n'])
        # Пункт должен БЫТЬ на экране карточки, удаление остаётся последним.
        self.assertIn('7) Страница подключения', out, out[-2500:])
        self.assertIn('8) Подписка', out, out[-2500:])
        self.assertIn('9) Удалить', out, out[-2500:])
        self.assertTrue('Страниц у этого пользователя нет' in out or 'нет функции pages_list' in out, out[-3000:])
        if 'нет функции pages_list' not in out:
            self.assertIn('1) Создать страницу', out, out[-3000:])

    def test_lite_without_a_domain_says_so_before_any_question_and_offers_ways_without_a_page(self):
        # NC-09: 1 — создать; объяснение и пути без страницы ДО вопроса о сроке; 0 — назад (без паузы)
        out = self.stand.keys(['1\n', '1\n', '7\n', '1\n', '0\n', '0\n', '0\n', '0\n', '0\n'])
        if 'нет функции pages_list' in out:
            self.skipTest('ядро страниц подключения не подключено')
        flat = ' '.join(out.split())
        self.assertIn('Страница подключения работает только на сайте с доменом и настоящим сертификатом, а у этого '
                      'сервера домена нет.', flat, out[-3000:])
        self.assertIn('Появится домен — появятся и страницы: «Настройки → Режим и сайт → Сменить режим».', flat)
        self.assertIn('Как отдать ключ без страницы:', flat)
        self.assertIn('1) Временная ссылка на файл — откройте её на телефоне пользователя', flat)
        # Вопроса о сроке нет вовсе, команд моста — тоже
        self.assertNotIn('Сколько работать странице?', out)
        for cli in ('change-mode', 'share-create', '--mode=', 'user-enable'):
            self.assertNotIn(cli, out)
        # Ни адреса, ни QR — страницы нет.
        self.assertNotIn('/p-', out)

    def test_without_a_domain_the_link_way_leads_to_the_file_choice(self):
        # 1 — создать; 1 — временная ссылка → профиль 1 → «Что положить в файл?» → 0 — назад; назад до выхода
        out = self.stand.keys(['1\n', '1\n', '7\n', '1\n', '1\n', '1\n', '0\n', '0\n', '0\n', '0\n', '0\n'])
        if 'нет функции pages_list' in out:
            self.skipTest('ядро страниц подключения не подключено')
        self.assertIn('Что положить в файл?', out.split('Как отдать ключ без страницы:', 1)[-1], out[-3000:])

    def test_with_the_panel_the_second_way_is_the_tunnel(self):
        admin = pathlib.Path(self.stand.env['ADMIN_DIR'])
        admin.mkdir(parents=True, exist_ok=True)
        (admin / 'server.py').write_text('# служба панели стоит\n', encoding='utf-8')
        try:
            out = self.stand.keys(['1\n', '1\n', '7\n', '1\n', '2\n', '\n', '0\n', '0\n', '0\n', '0\n'])
        finally:
            (admin / 'server.py').unlink()
        if 'нет функции pages_list' in out:
            self.skipTest('ядро страниц подключения не подключено')
        self.assertIn('2) QR и файл в браузере — через веб-панель и SSH-туннель', ' '.join(out.split()), out[-3000:])
        self.assertRegex(out.split('Как отдать ключ без страницы:', 1)[-1], r'ssh -L 1985:127\.0\.0\.1:1985 root@')


class MirrorScreen(unittest.TestCase):
    """Настройки → «Копии приложений для клиентов» (docs/CLIENTS.md §8): открывается, называет
    занятое место и предлагает скачать сборку; «Удалить всё» остаётся последним."""

    def test_settings_item_opens_the_mirror_and_returns(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(['5\n', '6\n', '0\n', '\n', '0\n', '0\n'])
        finally:
            stand.close()
        self.assertIn('6) Копии приложений для клиентов', out, out[-2500:])
        self.assertIn('7) Удалить всё', out, out[-2500:])
        self.assertTrue('Копий на сервере нет' in out or 'нет функции mirror_list' in out, out[-3000:])
        if 'нет функции mirror_list' not in out:
            self.assertIn('Занято 0 из', out, out[-3000:])
            self.assertIn('1) Скачать сборку на сервер', out, out[-3000:])


class ProfileInstallSubnet(unittest.TestCase):
    """Подсеть при установке профиля из меню: подсказка про туннели роутера к другим серверам, Enter — подсеть
    по умолчанию, своя — ложится в config.json. Раньше подсеть при установке не спрашивалась вовсе, и уйти с
    10.8.1.x, занятой туннелями роутера к другому серверу, можно было только перенумерацией после выдачи ключей."""

    def test_custom_subnet_is_asked_and_stored(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            self.assertFalse(stand.config().get('profiles', {}).get('awg1', {}).get('installed_at'))
            # Протоколы (3) → awg1 (3) → «Установить» (1) → порт Enter → подсеть 10.18.1.0/24 → пауза
            out = stand.keys(['3\n', '3\n', '1\n', '\n', '10.18.1.0/24\n', '\n',
                              '0\n', '0\n', '0\n', '0\n'], seconds=180)
            m = re.search(r'Подсеть для AmneziaWG 1\.5 \(Enter — ([0-9./]+), 0 — назад\)', out)
            self.assertIsNotNone(m, out[-4000:])
            self.assertEqual(m.group(1), '10.8.1.0/24')
            self.assertIn('туннели с такими адресами', ' '.join(out.split()))
            self.assertEqual(stand.config().get('profiles', {}).get('awg1', {}).get('subnet'), '10.18.1.0/24', out[-3000:])
        finally:
            stand.close()


class PortChangeScreen(unittest.TestCase):
    """§1d.2 №5: «сменить порт» предлагал текущий и сам же его отвергал."""

    def test_suggested_port_is_free_and_the_current_one_is_refused_by_name(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            cur = stand.config()['profiles']['awg2']['port']
            # Протоколы (3) → awg2 (2) → «Сменить порт» (1) → нынешний порт
            out = stand.keys(['3\n', '2\n', '1\n', '%d\n' % cur,
                              '0\n', '0\n', '0\n', '0\n', '0\n', '0\n'])
            self.assertIn('Сейчас профиль слушает порт %d' % cur, out, out[-4000:])
            self.assertIn('это и есть нынешний порт профиля', out, out[-4000:])
            # Предложенный порт — не нынешний
            m = re.search(r'UDP-порт для AmneziaWG 2\.0 · порт %d \(Enter — (\d+), 0 — назад\)' % cur, out)
            self.assertIsNotNone(m, out[-4000:])
            self.assertNotEqual(int(m.group(1)), cur)
        finally:
            stand.close()


class TrafficScreenLabels(unittest.TestCase):
    """§1d.2 №6: две несравнимые величины стояли под одной подписью."""

    def test_both_tables_say_what_they_count_and_the_period_summary_is_there(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Состояние (2) → Трафик и онлайн (2)
            out = stand.keys(['2\n', '2\n', '\n', '0\n', '0\n'])
            self.assertIn('обнуляются при каждом его перезапуске', out, out[-4000:])
            self.assertIn('Сумма за всё время', out, out[-4000:])
            self.assertIn('За сегодня', out, out[-4000:])
            self.assertIn('За месяц', out, out[-4000:])
        finally:
            stand.close()


class NoviceScreensAfterLazyPro(unittest.TestCase):
    """NC6-04, NC6-08, NC6-09 (критик-новичок на 1.0.5): «Трафик и онлайн» — у всех «○ давно не выходил», и у тех,
    кто ни разу не подключался, без строки «сколько на связи»; в шапке «● awg2 :443» без линии, в «Статусе» версия
    образа «3.1.2026…» читалась как линия 3.1; после Ленивого Pro шапка не говорила, откуда Pro."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        fixture = cls.stand.dir / 'awg_show.txt'
        fixture.write_text('', encoding='utf-8')
        cls.stand.env['GOAMNEZIA_AWG_SHOW_FIXTURE'] = str(fixture)
        cls.stand.keys(LITE_DEFAULTS)
        subprocess.run([BASH, str(ROOT / 'install.sh'), '--action=user-add', '--name=bob', '--profiles=awg3', '--json'],
                       env=cls.stand.env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        users = {u['name']: u for u in cls.stand.users().get('users', [])}
        cls.have_bob = 'bob' in users
        now = int(time.time())
        rows = ['%s\t(none)\t198.51.100.7:5000\t10.8.3.2/32\t%d\t1000\t2000\t0'
                % (users['admin']['peers']['awg3']['pubkey'], now - 30)]
        if cls.have_bob:
            rows.append('%s\t(none)\t(none)\t10.8.3.3/32\t0\t0\t0\t0' % users['bob']['peers']['awg3']['pubkey'])
        fixture.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        # Состояние → Трафик и онлайн → пауза → Статус → пауза → назад → выход
        cls.out = cls.stand.keys(['2\n', '2\n', '\n', '1\n', '\n', '0\n', '0\n'], seconds=90)
        cls.flat = ' '.join(cls.out.split())
        # Сайт на домене, который поставил Ленивый Pro (config.json .lazy.domain) — шапка так и подписывает режим
        cfg = cls.stand.config()
        cfg.update({'mode': 'pro', 'domain': 'vpn.example.com', 'lazy': {'domain': 'vpn.example.com'}})
        cfg.setdefault('site', {})['cert'] = 'letsencrypt'
        (cls.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        cls.head = cls.stand.keys(['0\n'], seconds=40)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_traffic_says_how_many_are_online_and_who_never_connected(self):
        self.assertTrue(self.have_bob, 'второй пользователь не завёлся')
        self.assertIn('Сейчас на связи: 1 из 2', self.flat, self.out[-4000:])
        self.assertRegex(self.flat, r'● admin \(Администратор\)\s+↓')
        self.assertRegex(self.flat, r'– bob\s+↓ 0')
        self.assertIn('– ещё не подключался', self.flat)

    def test_header_shows_the_line_next_to_the_profile(self):
        # Шапка: линия и порт профиля своей колонкой — «AmneziaWG 3.1 :6714», без имени места (решение владельца 15.10)
        header = self.out.split('1) Пользователи', 1)[0]
        cfg = self.stand.config()
        for prof, line in (('awg3', '3.1'), ('awg2', '2.0')):
            self.assertRegex(header, r'Профили:.* AmneziaWG %s :%d\b' % (re.escape(line), cfg['profiles'][prof]['port']),
                             header[-1500:])
        self.assertNotRegex(header, r'awg[123]', header[-1500:])

    def test_status_calls_the_image_an_image(self):
        status = self.out.split('Что стоит на сервере и в каком состоянии.', 1)[-1].split('Нажмите Enter', 1)[0]
        self.assertIn('образ — сборка ядра Amnezia в Docker', ' '.join(status.split()), status[:1500])
        rows = [l for l in status.splitlines() if 'UDP ' in l]
        self.assertTrue(rows, status[:1500])
        for row in rows:
            self.assertNotRegex(row, r'UDP \d+\s+\d+\.\d+\.\d+', row)
            self.assertRegex(row, r'UDP \d+\s+(образ \S+|-)', row)

    def test_header_says_pro_came_from_lazy_pro(self):
        self.assertIn('Режим: Pro (поставлен Ленивым Pro)', ' '.join(self.head.split()), self.head[-3000:])


class SettingsSayWhatTheyAffect(unittest.TestCase):
    """§1d.2 №7: настройки «только для новых ключей» об этом молчали."""

    def test_dns_screen_says_it_only_affects_new_keys(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(['5\n', '2\n', '1\n', '1\n', '\n', '0\n', '0\n', '0\n'], seconds=45)
            self.assertIn('Это действует только на ключи, выданные с этой минуты', out, out[-4000:])
        finally:
            stand.close()

    def test_endpoint_is_checked_before_it_is_written(self):
        stand = Stand()
        # Стенд в сеть не ходит: ответ DNS подставной — записей нет (0.4.3). Раньше
        # проверка шла в настоящий DNS машины разработчика.
        stand.env['GOAMNEZIA_DNS_STUB'] = ''
        # dig — зависимость ядра (lib/deps.sh, пакет dnsutils), на сервере он стоит. Без него продукт
        # домен не проверяет, а пишет как есть («Нет dig — проверить домен нечем») — и на машине без
        # dnsutils (Linux CI) тест падал на машине, а не на продукте. Поэтому dig у стенда свой и на
        # всех машинах один: продукт только убеждается, что dig есть, а ответ берёт из
        # GOAMNEZIA_DNS_STUB. Подставной dig записывает каждый вызов — их должно быть ноль, иначе
        # стенд пошёл бы в настоящий DNS.
        fake_bin = stand.tmp / 'bin'
        fake_bin.mkdir()
        dig_calls = stand.tmp / 'dig.calls'
        (fake_bin / 'dig').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$GA_TEST_DIG_CALLS"\nexit 9\n',
                                      encoding='utf-8')
        (fake_bin / 'dig').chmod(0o755)
        stand.env['GA_TEST_DIG_CALLS'] = str(dig_calls)
        stand.env['PATH'] = os.pathsep.join([str(fake_bin), stand.env.get('PATH', '')])
        try:
            require_exec(fake_bin / 'dig')
            stand.keys(LITE_DEFAULTS)
            # Настройки (5) → «Адреса для клиентов» (2) → «Адрес сервера в ключах» (2) → «Другой домен или
            # IP» (2: в Lite пункта «IP сервера» нет) → домен, которого нет; на вопрос «всё равно?» — нет
            out = stand.keys(['5\n', '2\n', '2\n', '2\n', 'no-such-domain.invalid\n', '0\n',
                              '\n', '0\n', '0\n', '0\n'], seconds=45)
            # Экран говорит, что стоит в ключах сейчас и откуда, а не задаёт голый вопрос
            self.assertIn('Сейчас в ключах', out, out[-4000:])
            self.assertIn('По умолчанию — IP сервера', out, out[-4000:])
            # «Нет dig» не принимается: dig у стенда есть, и эта строка значила бы, что проверка
            # не подключилась. Записей нет — экран обязан сказать, что домен никуда не ведёт.
            self.assertNotIn('Нет dig', out, out[-4000:])
            self.assertIn('никуда не разрешается', out, out[-4000:])
            # Отказались — в конфиг ничего не легло
            self.assertEqual(stand.config().get('endpoint_host', ''), '')
            # Ответ дал подставной DNS, настоящий dig не звали
            calls = dig_calls.read_text(encoding='utf-8') if dig_calls.exists() else ''
            self.assertEqual(calls, '', calls)
        finally:
            stand.close()


class BackupScreen(unittest.TestCase):
    """§1d.2 №13-14: «когда была последняя копия» и расписание."""

    def test_backup_screen_answers_when_and_lets_the_schedule_change(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(['4\n', '2\n', '0\n', '0\n', '0\n'])
            self.assertIn('Копий ещё не было', out, out[-4000:])
            self.assertIn('Расписание ночной копии', out, out[-4000:])
            self.assertIn('Сделать копию сейчас', out, out[-4000:])
            # Сняли копию — экран отвечает уже датой, а не «копий не было»
            out = stand.keys(['4\n', '2\n', '1\n', '\n', '0\n', '0\n', '0\n'])
            # NC-11: «Последняя копия — 1 минуту назад (… по времени сервера): имя», без UTC
            self.assertRegex(' '.join(out.split()),
                             r'Последняя копия — 1 минуту назад \(20\d\d-\d\d-\d\d \d\d:\d\d по времени сервера\): ')
            self.assertNotIn('UTC', out)
        finally:
            stand.close()


class ProfileRepairItem(unittest.TestCase):
    """§1d.2 №10: пропавший контейнер лечится, а кнопки не было."""

    def test_profile_card_has_the_repair_item(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Протоколы (3) → awg2 (2)
            out = stand.keys(['3\n', '2\n', '0\n', '0\n', '0\n'])
            self.assertIn('Поднять контейнер заново (из копии состояния)', out, out[-4000:])
        finally:
            stand.close()


class DnsScreenDoesNotOverwriteSilently(unittest.TestCase):
    """Экран «Настройки → DNS» переписывал настроенное на Enter, «0» и опечатку.

    Так настроенный 8.8.8.8 молча становился Cloudflare: писался аудит,
    печаталось «сохранено», и человек об этом не узнавал. Enter в мастере
    первой установки — другое дело, там умолчание законно.
    """

    def _dns(self, stand):
        return (stand.config().get('client_dns') or [])

    def test_enter_zero_and_a_typo_change_nothing(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # ставим заведомо своё значение, чтобы увидеть подмену
            stand.keys(['5\n', '2\n', '1\n', '2\n', '\n', '0\n', '0\n', '0\n'], seconds=45)
            before = self._dns(stand)
            self.assertTrue(before, 'не удалось задать исходное значение')
            # «4» — свой адрес, потом «0» (передумал). Этот уровень прошлая
            # починка пропустила, и он переписывал настройку молча.
            stand.keys(['5\n', '2\n', '1\n', '4\n', '0\n', '\n', '0\n', '0\n', '0\n'], seconds=45)
            self.assertEqual(self._dns(stand), before, 'отказ внутри «свой адрес» переписал настройку')

            for key in ('\n', '0\n', 'zzz\n'):
                stand.keys(['5\n', '2\n', '1\n', key, '\n', '0\n', '0\n', '0\n'], seconds=45)
                self.assertEqual(self._dns(stand), before,
                                 'ввод %r переписал настройку' % key)
        finally:
            stand.close()



class AgentsExtendItem(unittest.TestCase):
    """Продлить срок доступа было нечем ни в одном интерфейсе SSH-меню.

    Тревоги бота («срок на исходе», «доступ истёк») дословно ведут человека сюда:
    «Настройки → Доступы агентов → карточка доступа → Продлить». Пункта там не
    было, хотя и модуль `agents_extend`, и действие моста `agent-extend` давно
    работали, а строки словаря под пункт были написаны и не запрашивались ни
    одной строкой кода. Здесь настоящий экран в pty: пункт виден в списке,
    спрашивает дни, переспрашивает и двигает срок в agents.json.
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)
        # Доступ заводим мостом: экран продления должен работать над записью, а
        # выдача доступа здесь не предмет проверки. В Lite без сайта транспорту
        # нужен свой TLS-порт, иначе доступ выдавать некуда (needs_site).
        cls.bridge('mcp-http-on', '--port=4433')
        cls.grant = cls.bridge('agent-add', '--label=Агент для продления',
                               '--scope=read', '--days=30')
        cls.agents_path = cls.stand.dir / 'agents.json'

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    @classmethod
    def bridge(cls, action, *args):
        out = subprocess.run([BASH, str(ROOT / 'install.sh'), '--action=' + action, '--json'] + list(args),
                             env=cls.stand.env, cwd=str(ROOT), stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=120).stdout.decode('utf-8', 'replace')
        return json.loads(out.strip().splitlines()[-1])

    def record(self):
        doc = json.loads(self.agents_path.read_text(encoding='utf-8'))
        return [a for a in doc['agents'] if a['id'] == self.grant['id']][0]

    def reset(self, days=30):
        """Известный срок перед каждой проверкой: порядок тестов не должен решать."""
        self.bridge('agent-extend', '--id=' + self.grant['id'], '--days=%d' % days)
        return self.record()

    # Настройки (5) → Кто управляет (3) → Доступы агентов (3) → строка №1
    # открывает карточку → «Продлить срок» (4) → дни …
    def menu(self, *answers):
        return self.stand.keys(['5\n', '3\n', '3\n', '1\n', '4\n'] + list(answers)
                               + ['0\n', '0\n', '0\n', '0\n', '0\n'], seconds=90)

    def test_item_is_on_the_screen_and_extends(self):
        before = self.reset()
        out = self.menu('7\n', 'y\n')
        # Пункт должен БЫТЬ на экране карточки, а не только отзываться на
        # нажатие: человек не угадывает номера, он читает их с экрана.
        self.assertIn('4) Продлить срок доступа', out, out[-2500:])
        self.assertIn('На сколько дней продлить', out, out[-3000:])
        self.assertIn('Продлить доступ %s на 7 дней?' % self.grant['id'], out, out[-3000:])
        after = self.record()
        self.assertNotEqual(after['expires'], before['expires'], out[-3000:])
        self.assertEqual(after['term_days'], 7, out[-3000:])
        # Продление не трогает ни адрес, ни токен — иначе клиента агента
        # пришлось бы перенастраивать, а этого пункт как раз и избегает.
        self.assertEqual(after['secret'], before['secret'])
        self.assertEqual(after['token'], before['token'])

    def test_refusal_at_the_confirmation_changes_nothing(self):
        before = self.reset()
        out = self.menu('7\n', 'n\n')
        self.assertEqual(self.record()['expires'], before['expires'], out[-3000:])

    def test_zero_asks_the_weakening_question(self):
        before = self.reset()
        # Ноль — не «продлить на ноль дней», а снятие срока: спрашивается тем же
        # вопросом, что и при выдаче, а отказ оставляет срок на месте.
        out = self.menu('0\n', 'n\n')
        self.assertIn('Бессрочный доступ переживёт любую утечку', out, out[-3000:])
        self.assertEqual(self.record()['expires'], before['expires'], out[-3000:])
        out = self.menu('0\n', 'y\n')
        self.assertEqual(self.record()['expires'], '', out[-3000:])

    def test_a_bad_number_of_days_is_named_not_swallowed(self):
        before = self.reset()
        out = self.menu('99999\n')
        self.assertIn('Нужно целое число 0–3650.', out, out[-3000:])
        self.assertEqual(self.record()['expires'], before['expires'], out[-3000:])


class PromptsNameTheWayOutAndTheCurrentValue(unittest.TestCase):
    """§3.10: вопрос не говорил ни про выход, ни про нынешнее значение.

    «Подсеть профиля, например 10.8.2.0/24» — а какая сейчас? И как выйти?
    «0» работал всегда, но знали об этом только те экраны, где подсказку
    дописали руками.
    """

    def test_subnet_asks_with_the_current_value_and_says_zero_goes_back(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            cur = stand.config()['profiles']['awg2']['subnet']
            # Протоколы (3) → awg2 (2) → «Подсеть профиля» (6) → 0 (назад)
            out = stand.keys(['3\n', '2\n', '6\n', '0\n',
                              '0\n', '0\n', '0\n', '0\n', '0\n'])
            self.assertIn('сейчас %s' % cur, out, out[-3000:])
            # LC-17: «[0 — назад]» — одним куском: внутри неразрывные пробелы, перенос посреди подсказки невозможен.
            self.assertIn('[0\xa0—\xa0назад]', out, out[-3000:])
        finally:
            stand.close()

    def test_mtu_asks_with_the_current_value(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(['3\n', '2\n', '5\n', '0\n',
                              '0\n', '0\n', '0\n', '0\n', '0\n'])
            self.assertIn('MTU профиля, сейчас', out, out[-3000:])
            self.assertIn('[0 — назад]', out.replace('\xa0', ' '), out[-3000:])
        finally:
            stand.close()

    def test_preset_list_has_the_back_row(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Протоколы (3) → awg2 (2) → «Пересоздать» (7) → 0 (назад)
            out = stand.keys(['3\n', '2\n', '7\n', '0\n',
                              '0\n', '0\n', '0\n', '0\n', '0\n'])
            self.assertRegex(out, r'0\)\s+«?\s*Назад', out[-3000:])
        finally:
            stand.close()


class S4FixAndMtuStandard(unittest.TestCase):
    """S4-FIX (MTU-01, решение владельца 08.10). Выданный профиль 2.0 с большим S4 (генератор до 1.0.12 давал 15–150): на
    168 при S4 = 142 скачивание на пути 1456 — 0,0 Мбит/с. Шапка меню и «Протоколы» называют это, карточка профиля —
    пункт 12 «Исправить S4»: что будет — до вопроса цифрами, люди приложения Amnezia без ключа на сервере — набор имени
    профиля (ACC-2), после — кому что делать. MTU 1280 — стандарт и минимум: окно MTU говорит это, Enter — вернуть
    1280, ниже 1280 — отказ словами (L-02б), предупреждает только о том, что ломается наверняка (пакет больше 1440,
    L-02а)."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)
        # gleb — подхвачен у приложения Amnezia: ключ только у него в приложении (приватного на сервере нет).
        bridge(cls.stand, '--action=user-add', '--name=gleb', '--profiles=awg2')
        users = cls.stand.users()
        for u in users.get('users', []):
            if u.get('name') == 'gleb':
                u['imported'] = True
                u['peers']['awg2'].update({'privkey': '', 'imported': True})
        (cls.stand.dir / 'users.json').write_text(json.dumps(users), encoding='utf-8')
        # Выданный до 1.0.12 профиль 2.0: S4 = 142 (размеры 148+40, 92+60, 64+30, 32+142 различны).
        cfg = cls.stand.config()
        cfg['profiles']['awg2']['params'].update({'S1': '40', 'S2': '60', 'S3': '30', 'S4': '142'})
        (cls.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_1_header_and_protocols_name_the_risk(self):
        out = self.stand.keys(['3\n', '0\n', '0\n'], seconds=60)
        flat = ' '.join(out.split())
        port = self.stand.config()['profiles']['awg2']['port']
        # Профиль — подписью с портом; в «Протоколах» порт стоит своей колонкой «UDP N» (решение владельца 15.10)
        self.assertIn('Профиль AmneziaWG 2.0 · порт %d: на части сетей (мобильный интернет, PPPoE, некоторые хостеры) '
                      'скачивание не идёт — слишком большие пакеты (S4 = 142).' % port, flat, out[-3000:])
        self.assertIn('Исправить: «Протоколы» → профиль → «Исправить S4».', flat, out[-3000:])
        self.assertRegex(out, r'2\) ⚠ AmneziaWG 2\.0\s+UDP %d\s+⚠ S4 = 142' % port, out[-3000:])
        self.assertRegex(out, r'1\) \S+ AmneziaWG 3\.1\s+UDP \d+\n', out[-3000:])
        self.assertNotRegex(out, r'AmneziaWG 3\.1\s+UDP \d+\s+⚠ S4', 'у 3.1 (S = 12) риска нет')

    def test_2_cancel_changes_nothing(self):
        out = self.stand.keys(['3\n', '2\n', '11\n', '0\n', '\n', '0\n', '0\n', '0\n'], seconds=90)
        flat = ' '.join(out.split())
        # D-7: пункт починки — следом за 10, «Удалить» — последним
        self.assertIn('Исправить — пункт 11.', flat, out[-3000:])
        self.assertRegex(out, r'10\) [^\n]+\n\s+11\) Исправить S4\n\s+12\) Удалить\n', out[-3000:])
        self.assertIn('S4 станет 12–24 — при MTU 1280 пакет данных не больше 1384 байт, с запасом до 1400.', flat, out[-3000:])
        self.assertNotIn('S4 станет не больше 40', flat)
        self.assertIn('после исправления покажу, кому', flat, out[-3000:])
        # R8-2: о подписке — словами итога и окон перевыпуска (_user_sub_within), а не «в течение 12 часов»
        self.assertIn('подтянут новые настройки сами (до 12 ч', flat, out[-3000:])
        self.assertIn('или по «Обновить»', flat, out[-3000:])
        self.assertNotIn('в течение 12 часов', flat, out[-3000:])
        self.assertIn('Исправить S4 у AmneziaWG 2.0 · порт %d? [1 — да, 0 — нет]'
                      % self.stand.config()['profiles']['awg2']['port'], out.replace('\xa0', ' '), out[-3000:])
        self.assertEqual(self.stand.config()['profiles']['awg2']['params']['S4'], '142')
        self.assertNotIn('DRY: docker restart amnezia-awg2', out)

    def test_3_fix_asks_for_the_app_people_and_says_who_does_what(self):
        port = self.stand.config()['profiles']['awg2']['port']
        out = self.stand.keys(['3\n', '2\n', '11\n', '1\n', ('until', 'порт профиля'), '%d\n' % port, ('until', 'Нажмите Enter'), '\n',
                               '0\n', '0\n', '0\n'], seconds=150)
        flat = ' '.join(out.split())
        params = self.stand.config()['profiles']['awg2']['params']
        s4 = int(params['S4'])
        self.assertTrue(12 <= s4 <= 24, (s4, out[-3000:]))  # S4-MARGIN: «Исправить S4» — 12–24
        self.assertEqual(len({148 + 40, 92 + 60, 64 + 30, 32 + s4}), 4)
        port = self.stand.config()['profiles']['awg2']['port']
        # Люди приложения — до набора имени: что с ними будет и что им делать.
        self.assertIn('AmneziaWG 2.0 · порт %d подхвачен у приложения Amnezia: у пользователей на нём (1) ключи выдало приложение'
                      % port, flat, out[-3000:])
        self.assertIn('S4 исправлен.', flat, out[-3000:])
        self.assertIn('S4 профиля AmneziaWG 2.0 · порт %d: 142 → %d, пакет данных 1502 → %d байт — укладывается в 1400.'
                      % (port, s4, 1360 + s4), flat, out[-3000:])
        self.assertIn('Профиль перезапущен: клиенты переподключатся сами, обычно за 15–25 секунд.', flat, out[-3000:])
        self.assertIn('Выдавали ключ или файл (admin): импортировать заново', flat, out[-3000:])
        self.assertIn('Ключ из приложения Amnezia (gleb): прежний больше не подключится', flat, out[-3000:])
        # Сам перезапуск (DRY: docker restart) идёт под спиннером — его вывод на экран не попадает; проверен мостом
        # в tests/test_s4fix_dryrun.sh.
        # Риска больше нет: ни в шапке, ни в «Протоколах», ни пункта 12.
        out = self.stand.keys(['3\n', '2\n', '0\n', '0\n', '0\n'], seconds=60)
        self.assertNotIn('слишком большие пакеты', out)
        self.assertNotRegex(out, r'\d+\) Исправить S4')

    def test_4_mtu_window_says_1280_is_the_standard_and_minimum_and_warns_only_about_what_breaks(self):
        # S4 = 40 — верх безопасного (ручной предел; «Исправить S4» даёт случайный 12–24): 1320 + 80 + 40 = 1440 — без слов,
        # 1330 — уже пакет 1450.
        cfg = self.stand.config()
        cfg['profiles']['awg2']['params']['S4'] = '40'
        (self.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        # Протоколы → awg2 → MTU (5) → 1279 → отказ словами и тот же вопрос → 1500 → предупреждение и вопрос → 0 (нет)
        # → Enter; MTU (5) → Enter (вернуть 1280) → Enter; MTU (5) → 1320 (без вопросов) → Enter; выход.
        out = self.stand.keys(['3\n', '2\n', '5\n', '1279\n', ('until', 'оставьте 1280 или больше'), '1500\n',
                               ('until', 'всё равно?'), '0\n', '\n',
                               '5\n', '\n', '\n', '5\n', '1320\n', '\n', '0\n', '0\n', '0\n'], seconds=150)
        flat = ' '.join(out.split())
        self.assertIn('1280 — стандарт, работает везде. На проводном интернете можно поднять до 1320 — будет на '
                      'несколько процентов быстрее; если скачивание начнёт зависать, верните 1280.', flat, out[-3000:])
        self.assertIn('(1280–1500, Enter — вернуть 1280)', flat, out[-3000:])
        # L-02б: ниже 1280 туннель у клиентов с ::/0 не поднимается вовсе — отказ словами, MTU не записан.
        self.assertIn('Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — оставьте 1280 или больше.',
                      flat, out[-3000:])
        self.assertNotIn('MTU профиля: 1279.', flat)
        # L-02а: пакет больше 1440 — на части хостеров и сетей скачивание встанет.
        self.assertIn('Пакет 1620 байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше.', flat,
                      out[-3000:])
        self.assertIn('Сохранить MTU 1500 всё равно? [1 — да, 0 — нет]', out.replace('\xa0', ' '), out[-3000:])
        self.assertIn('MTU профиля: 1280.', flat, out[-3000:])
        self.assertIn('MTU профиля: 1320.', flat, out[-3000:])
        self.assertEqual(flat.count('всё равно?'), 1, '1320 при S4 ≤ 40 — без вопроса')
        self.assertEqual(self.stand.config()['profiles']['awg2']['params']['MTU'], '1320')
        # 1330 при S4 = 40 — пакет 1450: предупреждение и вопрос; «нет» — MTU прежний.
        out = self.stand.keys(['3\n', '2\n', '5\n', '1330\n', ('until', 'всё равно?'), '0\n', '\n', '0\n', '0\n', '0\n'],
                              seconds=90)
        flat = ' '.join(out.split())
        self.assertIn('Пакет 1450 байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше.', flat,
                      out[-3000:])
        self.assertNotIn('IPv6 через туннель перестанет работать', flat)
        self.assertEqual(self.stand.config()['profiles']['awg2']['params']['MTU'], '1320')

class RestartEverythingNamesWhatItRestarts(unittest.TestCase):
    """§3.12: список склеивался без пробелов, «IFS=', '» берёт первый знак."""

    def test_the_warning_separates_names_with_a_comma_and_a_space(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Обслуживание (4) → «Перезапустить всё» (1) → отказ (0)
            out = stand.keys(['4\n', '1\n', '0\n', '0\n', '0\n', '0\n'])
            m = re.search(r'Будет перезапущено: ([^\n]+)', out)
            self.assertIsNotNone(m, out[-3000:])
            names = m.group(1)
            self.assertNotRegex(names, r'\S,\S', 'имена склеены без пробела: %s' % names)
        finally:
            stand.close()


class DockerRestartAsksAndWarnsEveryone(unittest.TestCase):
    """DK-02: «Обслуживание → Перезапустить Docker» (пункт 8) — сначала что будет («VPN пропадёт на 10–30 секунд у
    всех»), потом вопрос; «нет» — Docker не трогается, «да» — служба и профили (в сухом прогоне — «DRY: systemctl
    restart docker»), итог словами."""

    def test_no_keeps_docker_and_yes_restarts_it(self):
        stand = Stand()
        try:
            # Контейнеры профилей на стенде «работают» (каталоги-фикстуры), Docker машины прогона не спрашивается.
            cfs = stand.tmp / 'cfs'
            for c in ('amnezia-awg2', 'amnezia-awg3'):
                (cfs / c).mkdir(parents=True)
            stand.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
            stand.env['GOAMNEZIA_DOCKER_SOCK'] = str(stand.tmp / 'nosuch.sock')
            stand.env.pop('DOCKER_HOST', None)
            stand.keys(LITE_DEFAULTS)
            # Обслуживание (4) → «Перезапустить Docker» (8) → «нет»
            out = stand.keys(['4\n', '8\n', 'n\n', '\n', '0\n', '0\n'])
            self.assertIn('8) Перезапустить Docker', out, out[-3000:])
            flat = ' '.join(out.split())
            self.assertIn('VPN пропадёт на 10–30 секунд у всех', flat, out[-3000:])
            self.assertIn('Перезапустить Docker?', flat)
            self.assertNotIn('DRY: systemctl restart docker', out)
            self.assertNotIn('Служба Docker перезапущена', out)
            # Теперь «да»: перезапуск и итог
            out = stand.keys(['4\n', '8\n', 'y\n', '\n', '0\n', '0\n'], seconds=90)
            self.assertIn('Служба Docker перезапущена', out, out[-3000:])
        finally:
            stand.close()


class AuditLogIsReadableFromTheMenu(unittest.TestCase):
    """§5.10: журнал действий читал только мост, человеку в меню взять «кто что
    сделал» было негде. И читается он СЛОВАМИ, а не построчным JSON."""

    def test_logs_screen_shows_the_action_log_in_words(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            audit = stand.tmp / 'audit.log'
            # В файле СМЕСЬ: строка от прежней версии (TSV) и новая (JSON).
            # Чужой формат не имеет права превращать непустой журнал в «пусто».
            audit.write_text(
                '2026-09-12T13:59:00Z\tssh\tbackup-create\t/opt/x.tar.gz\n'
                '{"ts":"2026-09-12T14:00:00Z","iface":"panel","stage":"call",'
                '"who":"panel:tester","action":"user-add","params":{"name":"anna"},'
                '"status":"success"}\n', encoding='utf-8')
            # Номер пункта зависит от того, что на сервере стоит: сначала читаем
            # список, потом жмём найденный номер — как это делает человек.
            listing = stand.keys(['2\n', '3\n', '0\n', '0\n', '0\n'])
            m = re.search(r'(\d+)\) журнал действий', listing)
            self.assertIsNotNone(m, listing[-2000:])
            out = stand.keys(['2\n', '3\n', '%s\n' % m.group(1), '\n', '0\n', '0\n', '0\n'])
            self.assertIn('журнал действий', out, out[-3000:])
            self.assertNotIn('записей ещё нет', out, 'журнал непустой: строка чужого формата не «пусто»')
            self.assertIn('backup-create', out, out[-3000:])   # старая строка дошла как есть
            self.assertIn('user-add', out, out[-3000:])
            self.assertNotIn('"stage"', out, 'человеку показываем словами, а не JSON')
        finally:
            stand.close()


class ModeSwitchKeepsItsPromise(unittest.TestCase):
    """«VPN и сайт» ↔ «Сайт и VPN на 443» (находки LS-01 и LS-02, 25.09.2026).

    Экран перехода обещал: «Откажетесь [от переноса порта] — и порт, и режим
    останутся прежними», а код всё равно переписывал режим, и меню жило с ярлыком
    «Сайт и VPN на 443» без профиля на 443. А «ключи при смене режима не меняются»
    было неправдой: домен в ключах ставится только в третьем режиме.
    """

    def _site_stand(self):
        stand = Stand()
        stand.keys(['3\n', 'example.com\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'], seconds=240)
        p = stand.dir / 'config.json'
        cfg = json.loads(p.read_text(encoding='utf-8'))
        cfg.setdefault('site', {})['cert'] = 'letsencrypt'
        p.write_text(json.dumps(cfg), encoding='utf-8')
        return stand

    def test_declining_the_port_move_changes_nothing(self):
        stand = self._site_stand()
        try:
            before = stand.config()
            # Настройки (5) → Режим и сайт (1) → Сменить режим (1) → Pro (2) → линия 2.0 (1) → перенос: 0 — нет
            out = stand.keys(['5\n', '1\n', '1\n', '2\n', '1\n', '0\n', '\n', '0\n', '0\n', '0\n', '0\n'])
            after = stand.config()
            self.assertIn('и порт, и режим останутся прежними', out, out[-3000:])
            self.assertEqual(after.get('mode'), 'lite_site', out[-3000:])
            self.assertEqual(after['profiles']['awg2']['port'], before['profiles']['awg2']['port'])
            self.assertNotIn('на UDP/443 не стоит ни один профиль', out, 'ярлык и порт не должны разойтись')
        finally:
            stand.close()

    def test_accepting_moves_the_port_and_says_keys_now_carry_the_domain(self):
        stand = self._site_stand()
        try:
            out = stand.keys(['5\n', '1\n', '1\n', '2\n', '1\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n'])
            after = stand.config()
            self.assertEqual(after.get('mode'), 'pro', out[-3000:])
            self.assertEqual(after['profiles']['awg2']['port'], 443, out[-3000:])
            self.assertIn('будет домен example.com', twp_clean(out), out[-3000:])
        finally:
            stand.close()


def twp_clean(text):
    """Переносы строк экрана не должны ломать поиск фразы."""
    return re.sub(r'\s+', ' ', text)


class LanguageChoiceSurvivesSecondLaunch(unittest.TestCase):
    """T21 (25.09.2026, было и в 0.4.2): английский, выбранный на первом экране,
    держался только до конца мастера — config.json создавался с зашитым lang=ru,
    а он читается раньше маркера выбора, и со второго запуска меню было русским."""

    def test_english_stays_english_after_the_wizard(self):
        stand = Stand(lang='en')
        try:
            stand.keys(['4\n', '\n', '\n', 'n\n', '\n', '0\n'], seconds=180)
            self.assertEqual(stand.config().get('lang'), 'en')
            out = stand.keys(['0\n'], seconds=60)
            self.assertIn('control panel', out, out[-2000:])
            self.assertNotIn('панель управления', out, out[-2000:])
        finally:
            stand.close()



class HeaderAddressTable(unittest.TestCase):
    """Шапка: таблица «Адреса сервера» всегда, с галочкой «A-запись» (просьба владельца 25.09.2026).

    Раньше таблица была только при двух адресах и больше. На машине с одним адресом — адрес и
    галочка; без домена — «—» и строка, что сверять не с чем; с доменом — ✓ или ✗ по A-записи.
    """

    def _header(self, stand):
        out = stand.keys(['0\n'], seconds=30)
        return out.split('Адреса сервера', 1)[1].split('1) Пользователи', 1)[0] if 'Адреса сервера' in out else ''

    def test_table_is_always_there_with_the_a_record_mark(self):
        stand = Stand()
        stand.env['GOAMNEZIA_DNS_STUB'] = ''
        try:
            stand.keys(LITE_DEFAULTS)
            # Адрес машины в сухом прогоне — подставной, как у сторожа
            stand.env['GOAMNEZIA_EDGE_IPS_STUB'] = '203.0.113.10'
            head = self._header(stand)
            self.assertIn('A-запись', head, head)
            self.assertIn('203.0.113.10', head, head)
            self.assertIn('домена нет', head, head)
            cfg = stand.config(); cfg['domain'] = 'vpn.example.com'
            (stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            stand.env['GOAMNEZIA_DNS_STUB'] = '203.0.113.10'
            head = self._header(stand)
            self.assertIn('✓', head, head)
            self.assertIn('vpn.example.com', head, head)
            stand.env['GOAMNEZIA_DNS_STUB'] = '198.51.100.7'
            head = self._header(stand)
            self.assertIn('✗', head, head)
        finally:
            stand.close()

    def test_narrow_window_gets_the_compact_table(self):
        """Два адреса и окно SSH с телефона (60 колонок): строка полной таблицы — до сотни знаков и
        рвалась посреди колонок. В узком окне — адрес, A-запись, сутки, пользователи и значок."""
        stand = Stand()
        stand.env['GOAMNEZIA_DNS_STUB'] = ''
        try:
            stand.keys(LITE_DEFAULTS)
            stand.env['GOAMNEZIA_EDGE_IPS_STUB'] = '203.0.113.10 203.0.113.11'
            stand.env['COLUMNS'] = '60'
            head = self._header(stand)
            self.assertIn('A-запись', head, head)
            self.assertIn('203.0.113.11', head, head)
            self.assertNotIn('15 мин', head, head)
            stand.env['COLUMNS'] = '120'
            head = self._header(stand)
            self.assertIn('15 мин', head, head)
        finally:
            stand.close()


class UsersScreenNumbersInOneColumn(unittest.TestCase):
    """Просьба владельца 25.09.2026: номера людей и номера пунктов — в одной колонке («1) ● admin» над
    «3) Добавить пользователя»), чтобы номера строк не терялись правее пунктов."""

    def test_row_and_item_numbers_share_the_column(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(['1\n', '0\n', '0\n'], seconds=40)
            # Экран списка — от шапки колонок («Действует до») до строки-подсказки «Номер строки…»
            screen = re.sub(r'\x1b\[[0-9;]*m', '', out.split('Номер строки', 1)[0])
            screen = screen.split('Действует до')[-1]
            cols = set()
            for line in screen.splitlines():
                m = re.match(r'^(\s*\d+)\) ', line)
                if m:
                    cols.add(len(m.group(1)))
            self.assertEqual(len(cols), 1, screen[-1500:])
            self.assertRegex(screen, r'\n\s+1\) \S+\s+admin')
            self.assertRegex(screen, r'\n\s+0\) ')
        finally:
            stand.close()


class KeyForAnotherAddress(unittest.TestCase):
    """«Ключ на другой адрес сервера (разово)» — экран ключей пользователя (docs/EDGEIPS.md §3.4).

    С двумя адресами у машины пункт есть: выбор адреса без «Нажмите Enter», строка «Выдача на адрес …»,
    и пункты 1–5 выдают тот же ключ с выбранным адресом внутри. С одним адресом пункта нет.
    """

    def test_one_off_address_goes_into_the_key(self):
        stand = Stand()
        stand.env['GOAMNEZIA_DNS_STUB'] = ''
        try:
            stand.keys(LITE_DEFAULTS)
            stand.env['GOAMNEZIA_EDGE_IPS_STUB'] = '203.0.113.10 203.0.113.11'
            # Пользователи → admin → Ключ → 6 (другой адрес) → 2 (203.0.113.11) → 2 (текст .conf) → профиль 1
            out = stand.keys(['1\n', '1\n', '1\n', '6\n', '2\n', '2\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n'],
                             seconds=60)
            self.assertIn('Ключ на другой адрес сервера (разово)', out, out[-3000:])
            self.assertIn('как обычно: 203.0.113.10', out, out[-3000:])
            self.assertIn('Выдача на адрес 203.0.113.11', out, out[-3000:])
            self.assertIn('адрес 203.0.113.11', out.split('Выдача на адрес 203.0.113.11', 1)[1], out[-3000:])
            self.assertRegex(out, r'Endpoint = 203\.0\.113\.11:\d+')
            # Выбор адреса — настройка экрана: паузы «Нажмите Enter» между выбором и меню нет
            between = out.split('как обычно: 203.0.113.10', 1)[1].split('Выдача на адрес 203.0.113.11', 1)[0]
            self.assertNotIn('Нажмите Enter', between, between[-1500:])
            # За пользователем ничего не сохранено
            users = json.loads((stand.dir / 'users.json').read_text(encoding='utf-8'))
            admin = [u for u in users['users'] if u['name'] == 'admin'][0]
            self.assertFalse(admin.get('endpoint_ip'), admin)
        finally:
            stand.close()

    def test_no_item_with_a_single_address(self):
        stand = Stand()
        stand.env['GOAMNEZIA_DNS_STUB'] = ''
        try:
            stand.keys(LITE_DEFAULTS)
            stand.env['GOAMNEZIA_EDGE_IPS_STUB'] = '203.0.113.10'
            out = stand.keys(['1\n', '1\n', '1\n', '0\n', '0\n', '0\n', '0\n'], seconds=40)
            self.assertIn('QR-код .conf', out, out[-2000:])
            self.assertNotIn('Ключ на другой адрес сервера', out, out[-2000:])
        finally:
            stand.close()


class ConnectionCheckAfterKey(unittest.TestCase):
    """Проверка подключения после ключа (находка критика продукта 25.09.2026).

    Мастер показывает ключ администратора; ждать свежего рукопожатия он начинает только по просьбе — пунктом
    «Проверить, что телефон подключился» (NC-05: раньше ждал сам, даже когда подключаться было нечем). На стенде —
    подставной вывод awg, пустой: рукопожатия нет. Ожидание не вешает мастер: Enter прерывает, по таймауту — что
    проверить, с портом профиля. Без подставного вывода на стенде ожидания нет вовсе.
    """

    def test_wizard_waits_on_request_and_names_the_port_or_stops(self):
        stand = Stand()
        fixture = stand.dir / 'awg_show_empty.txt'
        stand.dir.mkdir(parents=True, exist_ok=True)
        fixture.write_text('', encoding='utf-8')
        stand.env['GOAMNEZIA_AWG_SHOW_FIXTURE'] = str(fixture)
        stand.env['GOAMNEZIA_HANDSHAKE_WAIT_SECONDS'] = '3'
        try:
            # QR показан или нечем рисовать — пункты: панель, ссылка, проверка (3); после проверки — Enter, меню 0
            out = stand.keys(['4\n', '\n', '\n', 'n\n', '3\n', '\n', '0\n'], seconds=120)
            self.assertIn('3) Проверить, что телефон подключился', out, out[-3000:])
            self.assertIn('Жду подключения до 3 минут', out, out[-3000:])
            tail = out.split('Жду подключения до 3 минут', 1)[1]
            self.assertTrue('Ожидание прервано' in tail or 'Подключения не видно' in tail, tail[:2000])
            if 'Подключения не видно' in tail:
                self.assertRegex(tail, r'входящий UDP \d+')
            self.assertNotIn('рукопожат', out)
        finally:
            stand.close()

    def test_no_wait_on_the_stand_without_awg_output(self):
        stand = Stand()
        try:
            out = stand.keys(LITE_DEFAULTS, seconds=90)
            self.assertIn('Готово. Ключ администратора ниже.', out, out[-2000:])
            self.assertNotIn('Жду подключения', out, out[-3000:])
        finally:
            stand.close()


class ChangelogAfterBridgeMigration(unittest.TestCase):
    """LC-03: миграцию после обновления часто проводит тик таймера (мост), а «что нового» показывалось
    только тому процессу, который мигрировал сам, — человек его не видел никогда."""

    def test_menu_shows_whats_new_after_a_timer_migrated(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            cfg = stand.config(); cfg['version'] = '0.4.2'
            (stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            # Пишущее действие моста — как тик сторожа: мигрирует без вопросов
            subprocess.run([BASH, str(ROOT / 'install.sh'), '--action=user-add', '--name=tick', '--json'],
                           env=stand.env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            self.assertNotEqual(stand.config().get('version'), '0.4.2', 'мост не провёл миграцию')
            out = stand.keys(['\n', '0\n'], seconds=40)
            self.assertIn('Обновление 0.4.2 →', out, out[-3000:])
            self.assertIn('Что нового', out, out[-3000:])
            # Строки «что нового» меняются с каждым выпуском: сверяем с тем, что сейчас в словаре,
            # а не с текстом прошлой версии (иначе тест краснеет на каждом подъёме версии).
            menu_ru = (ROOT / 'lib' / 'lang' / 'ru.d' / 'menu.sh').read_text(encoding='utf-8')
            flat = ' '.join(out.split())
            lines = re.findall(r'^I18N\[upd_l\d+\]="• (.*)"$', menu_ru, re.M)
            self.assertTrue(lines, 'в словаре нет строк «что нового»')
            for line in (lines[0], lines[-1]):
                self.assertIn(' '.join(line.split()[:6]), flat, out[-3000:])
            # NC-28: без жаргона «контейнеров приложения»
            self.assertNotIn('контейнеров приложения', out)
            out = stand.keys(['0\n'], seconds=30)
            self.assertNotIn('Что нового', out, 'экран «что нового» показан второй раз')
        finally:
            stand.close()


RAW_TTY_ERRORS = ('/dev/tty', 'No such device', 'Device not configured')


class AnswersWithoutControllingTerminal(unittest.TestCase):
    """Ответы, которые берутся с управляющего терминала, когда его нет (находка L2-05).

    Стенд запускает install.sh через setsid: pty у процесса — stdin, но управляющим терминалом он не стал, и
    /dev/tty не открывается, хотя по правам файла `[ -r /dev/tty ]` истинно. Так бывает под setsid, из cron и
    из юнита. Прежде набранная фраза удаления не читалась (сырое «/dev/tty: No such device…» и «Отменено»), а
    ключ доступа оставался в очереди и уходил ответом на следующий вопрос.
    """

    def test_user_delete_reads_the_typed_name(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            self.assertIn('admin', [u['name'] for u in stand.users().get('users', [])])
            # 1 — пользователи, 1 — admin, 9 — удалить, имя, Enter — пауза, назад до выхода
            out = stand.keys(['1\n', '1\n', '9\n', 'admin\n', '\n', '0\n', '0\n'])
            names = [u['name'] for u in stand.users().get('users', [])]
            self.assertNotIn('admin', names, out[-2000:])
            for raw in RAW_TTY_ERRORS:
                self.assertNotIn(raw, out, out[-2000:])
        finally:
            stand.close()

    SECRET_SNIPPET = r'''
source <(sed -n '/^_read_secret_tty()/,/^}/p' "$1")
_read_secret_tty pat
printf '\nGOT=[%s]\n' "$pat"
IFS= read -r next || next="<eof>"
printf 'NEXT=[%s]\n' "$next"
'''

    def test_secret_is_read_from_stdin_terminal_without_echo(self):
        master, slave = pty.openpty()
        proc = subprocess.Popen([BASH, '-c', self.SECRET_SNIPPET, '_', str(ROOT / 'install.sh')],
                                stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                                preexec_fn=os.setsid)
        os.close(slave)
        out = b''
        try:
            time.sleep(0.5)
            os.write(master, b'pat-L2-05\n')
            time.sleep(0.3)
            os.write(master, b'after\n')
            deadline = time.time() + 20
            while time.time() < deadline:
                ready, _, _ = select.select([master], [], [], 0.5)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    out += chunk
                elif proc.poll() is not None:
                    break
        finally:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except Exception:
                pass
            os.close(master)
        text = clean(out.decode('utf-8', 'replace'))
        self.assertIn('GOT=[pat-L2-05]', text, text)
        self.assertIn('NEXT=[after]', text, text)          # ключ не остался ответом на следующий вопрос
        self.assertEqual(text.count('pat-L2-05'), 1, text)  # и не показан на экране (ввод без эха)
        for raw in RAW_TTY_ERRORS:
            self.assertNotIn(raw, text, text)

    def test_secret_is_not_taken_from_a_pipe(self):
        # Нет терминала вовсе (curl | bash): из конвейера ключ не берём — там тело скрипта, а не человек.
        r = subprocess.run([BASH, '-c', self.SECRET_SNIPPET, '_', str(ROOT / 'install.sh')],
                           input='secret\n', capture_output=True, text=True, timeout=30,
                           start_new_session=True)
        self.assertIn('GOT=[]', r.stdout, r.stdout + r.stderr)
        for raw in RAW_TTY_ERRORS:
            self.assertNotIn(raw, r.stderr, r.stderr)


class DryRunLeavesHostUnitsAlone(unittest.TestCase):
    """Сухой прогон не трогает юниты машины, на которой идёт (находка L2-09).

    На PATH — подставной systemctl, который «знает» любой юнит, отвечает «работает» и пишет каждый вызов в
    журнал: так выглядит машина разработчика или сервер, где продукт уже стоит. Прежде «Перезапустить всё»
    включало в план nginx и службы goamnezia-* этой машины и по «1» перезапускало их, экран бота показывал её
    бота «работает», а каждый запуск меню перезапускал её страницы и MCP. Теперь пробы в сухом прогоне отвечают
    «юнита нет», а действия печатаются строкой DRY.
    """

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)
        fake = cls.stand.tmp / 'fakebin'
        fake.mkdir()
        cls.log = cls.stand.tmp / 'systemctl.calls'
        sc = fake / 'systemctl'
        sc.write_text('#!/bin/sh\necho "$*" >> "%s"\nexit 0\n' % cls.log, encoding='utf-8')
        sc.chmod(0o755)
        cls.stand.env['PATH'] = '%s:%s' % (fake, cls.stand.env.get('PATH', ''))
        # 4 — обслуживание, 1 — перезапустить всё, 1 — да, Enter — пауза, 0 — назад;
        # 6 — Telegram-бот, 0 — назад; 0 — выход.
        cls.out = cls.stand.keys(['4\n', '1\n', '1\n', '\n', '0\n', '6\n', '0\n', '0\n'])
        cls.calls = cls.log.read_text(encoding='utf-8').splitlines() if cls.log.exists() else []

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_nothing_is_restarted_stopped_or_disabled(self):
        acts = [c for c in self.calls
                if c.split(' ', 1)[0] in ('restart', 'start', 'stop', 'reload', 'enable', 'disable',
                                          'daemon-reload', 'reset-failed', 'try-restart', 'reload-or-restart')]
        self.assertEqual(acts, [], '\n'.join(self.calls))

    def test_restart_plan_names_only_the_profiles(self):
        flat = ' '.join(self.out.split())
        i = flat.find('Будет перезапущено:')
        self.assertGreaterEqual(i, 0, self.out[-3000:])
        plan = flat[i:i + 300].split('Связь разорвётся', 1)[0]
        for unit in ('Telegram-бот', 'Панель', 'nginx'):
            self.assertNotIn(unit, plan, plan)

    def test_bot_screen_does_not_show_the_host_bot(self):
        flat = ' '.join(self.out.split())
        i = flat.rfind('Служба goamnezia-bot:')
        self.assertGreaterEqual(i, 0, self.out[-3000:])
        self.assertIn('не установлена', flat[i:i + 80], flat[i:i + 80])
        self.assertFalse([c for c in self.calls if 'goamnezia-bot' in c], '\n'.join(self.calls))


# ══ Критик-новичок на выпущенной 0.5.0 (test1, 28.09.2026): находки NC-… по SSH ══════════════════════════════

class NoviceFirstScreens(unittest.TestCase):
    """NC-03: первые экраны говорили на языке разработчика — «0) Оставить как есть» в самом первом выборе
    языка, «рабочая копия goamnezia-mcp создана/обновлена из эталона», вывод apt по-английски (последнее —
    в tests/test_core_dryrun.sh: на стенде apt не зовётся)."""

    def test_first_language_picker_has_nothing_to_keep_and_enter_is_russian(self):
        stand = Stand()
        try:
            (stand.dir / '.language').unlink()
            out = stand.keys(['\n'] + LITE_DEFAULTS)
            head = out.split('Режим установки', 1)[0]
            self.assertIn('1) Русский', head, head[-1500:])
            self.assertIn('2) English', head, head[-1500:])
            self.assertNotIn('Оставить как есть', head, head[-1500:])
            self.assertNotIn('Keep as is', head, head[-1500:])
            self.assertEqual((stand.dir / '.language').read_text(encoding='utf-8').strip(), 'ru')
            self.assertTrue(stand.config().get('installed'), out[-2000:])
        finally:
            stand.close()

    def test_settings_language_keeps_its_way_back(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # 5 — Настройки, 4 — Язык, 0 — оставить, Enter — пауза, 0 — назад, 0 — выход
            out = stand.keys(['5\n', '4\n', '0\n', '\n', '0\n', '0\n'])
            self.assertIn('Оставить как есть', out, out[-2000:])
            self.assertEqual((stand.dir / '.language').read_text(encoding='utf-8').strip(), 'ru')
        finally:
            stand.close()

    def test_component_update_line_is_in_words_and_the_installer_is_not_an_update(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            admin = pathlib.Path(stand.env['ADMIN_DIR'])
            (admin / 'static').mkdir(parents=True)
            # Рабочая копия — как её кладёт install_admin.sh: всё, кроме самого установщика. Обновлять нечего:
            # раньше «новым» считался установщик, и первый запуск после установки панели печатал «обновлена».
            for rel in ('server.py', 'README.md', 'static/app.js', 'static/index.html', 'static/styles.css'):
                shutil.copy(str(ROOT / 'admin-web' / rel), str(admin / rel))
            out = stand.keys(['0\n'], seconds=40)
            before_menu = out.split('панель управления', 1)[0]
            self.assertNotIn('обновлена', before_menu, before_menu[-1500:])
            self.assertFalse((admin / 'install_admin.sh').exists())
            # Вышла новая версия панели — одна строка словами, без «рабочей копии» и «эталона»
            (admin / 'server.py').write_text('# old\n', encoding='utf-8')
            out = stand.keys(['0\n'], seconds=40)
            before_menu = out.split('панель управления', 1)[0]
            # NC-28: целой фразой, а не «Установлена новая версия: веб-панель.»
            self.assertIn('Веб-панель обновлена.', before_menu, before_menu[-1500:])
            self.assertNotIn('Установлена новая версия', before_menu)
            self.assertNotIn('эталон', out)
            self.assertNotIn('Рабочая копия', out)
        finally:
            stand.close()


def fake_qrencode(stand, rows, cols):
    """Подставной qrencode: на любой текст — картинка rows×cols знаков. Так размер окна проверяется одинаково на
    любой машине, в том числе там, где настоящего qrencode нет."""
    fake = stand.tmp / 'fakeqr'
    fake.mkdir(exist_ok=True)
    q = fake / 'qrencode'
    q.write_text('#!/bin/sh\ncat >/dev/null\ni=0\nwhile [ "$i" -lt %d ]; do printf "%%s\\n" "%s"; i=$((i + 1)); done\n'
                 % (rows, '█' * cols), encoding='utf-8')
    q.chmod(0o755)
    require_exec(q)
    stand.env['PATH'] = '%s:%s' % (fake, stand.env.get('PATH', ''))


class QrSeriesMeasuredBeforeTheHeader(unittest.TestCase):
    """NC-06: серия QR печатала «QR 1 из 2», а следом «не помещается 117×59» — и вторая часть не показывалась.
    Теперь все куски меряются до первого заголовка; не влезает хоть один — ни одного заголовка, подсказка
    «уменьшите шрифт (Cmd/Ctrl −) или разверните окно» и ссылка текстом."""

    # Пользователи → admin → «Ключ: QR, файл, ссылка» → «Серия QR» → профиль 1 → пауза → назад до выхода
    KEYS = ['1\n', '1\n', '1\n', '4\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n']

    def test_too_big_series_prints_no_header_and_gives_the_hint_and_the_link(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            fake_qrencode(stand, 50, 60)
            stand.env['LINES'] = '40'
            out = stand.keys(self.KEYS)
            tail = out.split('Ключ приложения Amnezia: admin', 1)[1]
            self.assertNotRegex(tail, r'QR \d+ из \d+', tail[:3000])
            flat = ' '.join(tail.split())
            self.assertIn('QR-код не помещается в окно: нужно 62×52 знаков, а в окне 120×40.', flat, flat[:2000])
            self.assertIn('Cmd − на Mac, Ctrl − в Windows и Linux', flat)
            self.assertIn('vpn://', tail)
            self.assertNotIn('соседний пункт', out)
        finally:
            stand.close()

    def test_too_big_series_offers_the_ways_and_shows_the_series_again(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            fake_qrencode(stand, 50, 60)
            stand.env['LINES'] = '40'
            # Серия не влезла → пути; окно выросло → «1» — показать снова → серия целиком → Enter — назад
            out = stand.keys(['1\n', '1\n', '1\n', '4\n', '1\n', ('until', 'Как перенести ключ на телефон:'),
                              ('winsize', 60, 120), '1\n', '\n', '0\n', '0\n', '0\n', '0\n'])
            tail = out.split('Ключ приложения Amnezia: admin', 1)[1]
            self.assertIn('Как перенести ключ на телефон:', tail, tail[:3000])
            self.assertIn('1) Показать QR снова', tail)
            again = tail.split('Выбор (0 или Enter — дальше): 1', 1)[1]
            heads = re.findall(r'QR (\d+) из (\d+)', again)
            self.assertTrue(heads, again[:3000])
            self.assertEqual([int(a) for a, _ in heads], list(range(1, int(heads[0][1]) + 1)), heads)
        finally:
            stand.close()

    def test_a_series_that_fits_shows_every_part(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            fake_qrencode(stand, 20, 40)
            out = stand.keys(self.KEYS)
            heads = re.findall(r'QR (\d+) из (\d+)', out)
            self.assertTrue(heads, out[-3000:])
            total = int(heads[0][1])
            self.assertEqual([int(a) for a, _ in heads], list(range(1, total + 1)), heads)
            self.assertNotIn('не помещается', out)
        finally:
            stand.close()


class FirstKeyWhenTheQrDoesNotFit(unittest.TestCase):
    """NC-05 (блокирующая): QR первого ключа не влез в окно 100×40, а мастер три минуты ждал подключения, которого
    быть не могло, и отправлял к «соседнему пункту карточки», которой в мастере нет. Теперь не влез — сразу пути:
    показать снова (уменьшив шрифт или развернув окно), QR в браузере через панель и туннель, временная ссылка;
    ожидание подключения — только пунктом по просьбе. Enter по-прежнему ведёт в главное меню."""

    WIZARD = ['4\n', '\n', '\n', 'n\n']          # Lite, профили и порты как есть, без бота
    WAYS = ('until', 'Как перенести ключ на телефон:')   # экран путей нарисован — дальше пункты

    def _stand(self):
        stand = Stand()
        fixture = stand.dir / 'awg_show_empty.txt'
        fixture.write_text('', encoding='utf-8')
        # Подставной вывод awg: ждать было бы что — и всё равно мастер не ждёт сам
        stand.env['GOAMNEZIA_AWG_SHOW_FIXTURE'] = str(fixture)
        stand.env['GOAMNEZIA_HANDSHAKE_WAIT_SECONDS'] = '3'
        fake_qrencode(stand, 50, 60)
        stand.env['LINES'] = '40'
        return stand

    def test_three_ways_at_once_and_no_waiting(self):
        stand = self._stand()
        try:
            out = stand.keys(self.WIZARD + [self.WAYS, '\n', '0\n'])
            tail = out.split('Готово. Ключ администратора ниже.', 1)[1]
            flat = ' '.join(tail.split())
            self.assertIn('QR-код не помещается в окно', flat, flat[:3000])
            self.assertIn('Как перенести ключ на телефон:', flat)
            for item in ('1) Показать QR снова — сначала уменьшите шрифт (Cmd − или Ctrl −) или разверните окно',
                         '2) QR в браузере — через веб-панель и SSH-туннель',
                         '3) Временная ссылка на файл — перенести ключ на свой телефон за 5 минут',
                         '4) Проверить, что телефон подключился (подожду до 3 минут)',
                         '0) В главное меню'):
                self.assertIn(item, flat, flat[-3000:])
            self.assertNotIn('Жду подключения', out)
            self.assertNotIn('соседний пункт', out)
            # NC-27: над «QR-код не помещается» нет подписи о том, какие приложения этот QR читают
            self.assertNotIn('Этот QR и файл .conf читают', tail.split('Ключ vpn://', 1)[0])
            # Конфиг текстом не вываливается — ключ текстом есть и так (vpn://)
            self.assertNotIn('[Interface]', tail)
            self.assertIn('vpn://', tail)
            self.assertIn('панель управления', tail.split('Как перенести ключ на телефон:', 1)[1])
            self.assertTrue(stand.config().get('installed'))
        finally:
            stand.close()

    def test_show_again_after_the_window_grew(self):
        stand = self._stand()
        try:
            out = stand.keys(self.WIZARD + [self.WAYS, ('winsize', 60, 120), '1\n', '\n', '0\n'])
            after = out.split('Выбор (0 или Enter — дальше): 1', 1)[1]
            self.assertIn('█' * 60, after, after[:2000])
            self.assertNotIn('не помещается', after.split('█', 1)[0])
            # QR на экране — пункт «показать снова» больше не нужен
            menu = after.split('Ключ на экране. Что дальше:', 1)[1]
            self.assertNotIn('Показать QR снова', menu.split('Выбор', 1)[0])
        finally:
            stand.close()

    def test_panel_item_gives_the_tunnel_command(self):
        stand = self._stand()
        try:
            out = stand.keys(self.WIZARD + [self.WAYS, '2\n', '\n', '0\n'])
            self.assertIn('Выбор (0 или Enter — дальше): 2', out, out[-4000:])
            after = out.split('Выбор (0 или Enter — дальше): 2', 1)[1]
            self.assertRegex(after, r'ssh -L 1985:127\.0\.0\.1:1985 root@203\.0\.113\.10', after[:2000])
            self.assertIn('В панели откройте пользователя admin', ' '.join(after.split()))
        finally:
            stand.close()

    def test_link_item_goes_straight_to_the_file_choice(self):
        stand = self._stand()
        try:
            # 3 — временная ссылка; профиль уже известен (awg2): сразу «Что положить в файл?»; 0 — назад
            out = stand.keys(self.WIZARD + [self.WAYS, '3\n', '0\n', '\n', '0\n'])
            self.assertIn('Выбор (0 или Enter — дальше): 3', out, out[-4000:])
            after = out.split('Выбор (0 или Enter — дальше): 3', 1)[1]
            self.assertIn('Что положить в файл?', after, after[:2000])
            self.assertNotIn('Каким профилем выдать ключ', after.split('Что положить в файл?', 1)[0])
        finally:
            stand.close()


class AddUserLikeANovice(unittest.TestCase):
    """NC-07: «Мама телефон» получал «недопустимый идентификатор» без слова о русской подписи; выбор между 2.0 и
    3.1 — без совета; после ключа снова три минуты ожидания подключения и «рукопожатие» в тексте."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        fixture = cls.stand.dir / 'awg_show_empty.txt'
        fixture.write_text('', encoding='utf-8')
        cls.stand.env['GOAMNEZIA_AWG_SHOW_FIXTURE'] = str(fixture)
        cls.stand.env['GOAMNEZIA_HANDSHAKE_WAIT_SECONDS'] = '3'
        cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def _user(self, name):
        return next((u for u in self.stand.users().get('users', []) if u['name'] == name), None)

    def test_a_russian_name_becomes_the_label_and_a_latin_name_is_offered(self):
        # Пользователи → Добавить → «Мама телефон» → Enter (взять) → профили, лимит, срок как есть → ключ: нет
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'Мама телефон\n', '\n', '\n', '\n', '\n',
                               '0\n', '\n', '0\n', '0\n'])
        self.assertIn('✎ Мама телефон · mama_telefon — подпись как вы написали, имя латиницей', out, out[-3000:])
        self.assertNotIn('Недопустимый идентификатор', out)
        u = self._user('mama_telefon')
        self.assertIsNotNone(u, out[-3000:])
        self.assertEqual(u['label'], 'Мама телефон')
        # Второй раз то же имя — латинское с -2, подпись та же
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'Мама телефон\n', '\n', '\n', '\n', '\n',
                               '0\n', '\n', '0\n', '0\n'])
        self.assertIn('mama_telefon-2', out, out[-3000:])
        self.assertEqual(self._user('mama_telefon-2')['label'], 'Мама телефон')

    def test_a_name_without_letters_is_explained(self):
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], '!!!\n', '0\n', '\n', '0\n', '0\n'])
        self.assertIn('Из этого имени не выходит латинское', ' '.join(out.split()), out[-3000:])
        self.assertNotIn('Недопустимый идентификатор', out)

    def test_profiles_have_advice_and_the_key_does_not_wait_by_itself(self):
        # Добавить → vera → профили (с пояснениями) → лимит, срок → ключ: да → профиль 1 (3.1) → Enter — дальше
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[0], 'vera\n', '\n', '\n', '\n', '1\n', '1\n',
                               '\n', '0\n', '0\n'])
        flat = ' '.join(out.split())
        self.assertIn('самая сильная маскировка; нужна свежая версия приложения', flat, flat[-3000:])
        self.assertIn('Не знаете, что выбрать? Берите AmneziaWG 3.1', flat)
        self.assertIn('не подключится — возьмите 2.0', flat)
        self.assertIn('Проверить, что телефон подключился', flat)
        self.assertNotIn('Жду подключения', out)
        self.assertNotIn('рукопожат', out)
        # Enter на «что дальше» — сразу список, без второго «Нажмите Enter»
        between = out.split('Выбор (0 или Enter — дальше):', 1)[1].split('Номер строки — открыть карточку', 1)[0]
        self.assertNotIn('Нажмите Enter', between, between[-1500:])


class WithADomainThePageComesFirst(unittest.TestCase):
    """NC6-01, NC6-02, NC6-05: после «Добавить» с доменом «как перенести ключ» не предлагал страницу подключения,
    хотя строкой выше её называл; ссылку на файл владелец принимал за способ отдать ключ другу; срок страницы —
    за срок VPN. Теперь с доменом страница — первым пунктом (тот же поток, что пункт карточки), вопрос о ключе её
    называет, ссылка — «перенести ключ на свой телефон за 5 минут», а под «Сколько работать странице?» — что это
    не срок VPN."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        fixture = cls.stand.dir / 'awg_show_empty.txt'
        fixture.write_text('', encoding='utf-8')
        cls.stand.env['GOAMNEZIA_AWG_SHOW_FIXTURE'] = str(fixture)
        cls.stand.env['GOAMNEZIA_HANDSHAKE_WAIT_SECONDS'] = '3'
        cls.stand.keys(LITE_DEFAULTS)
        cfg = cls.stand.config()
        cfg.update({'mode': 'lite_site', 'domain': 'vpn.example.com'})
        cfg.setdefault('site', {})['cert'] = 'letsencrypt'
        (cls.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        # Добавить → dasha → профили, лимит, срок как есть → ключ: да → профиль 1 → «что дальше»: 1 (страница) →
        # срок 24 часа → «что дальше» снова: Enter → назад до выхода
        cls.out = cls.stand.keys(['1\n', '%d\n' % user_items(cls.stand)[0], 'dasha\n', '\n', '\n', '\n', '1\n', '1\n',
                                  '1\n', '2\n', '\n', '0\n', '0\n'], seconds=150)
        cls.flat = ' '.join(cls.out.split())

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_the_key_question_names_the_page(self):
        self.assertIn('Показать ключ для dasha прямо сейчас? Потом можно сделать и страницу подключения — ссылку '
                      'для друга.', self.flat, self.out[-4000:])

    def test_the_page_is_the_first_way_and_the_link_is_for_your_own_phone(self):
        menu = self.flat.split('Страница подключения для друга', 1)
        self.assertEqual(len(menu), 2, self.flat[-4000:])
        self.assertTrue(menu[0].rstrip().endswith('1)'), menu[0][-300:])
        self.assertIn('Временная ссылка на файл — перенести ключ на свой телефон за 5 минут', menu[1])

    def test_the_page_item_creates_a_page_and_says_its_expiry_is_not_the_vpn_one(self):
        after = self.flat.split('Страница подключения для друга', 1)[-1]
        self.assertIn('Сколько работать странице? Это срок самой страницы, а не VPN: ключ работает и после неё. '
                      'Ограничить доступ пользователю — «Срок действия» в его карточке.', after, after[:3000])
        self.assertRegex(after, r'https://vpn\.example\.com/p-[A-Za-z0-9_-]{43}/')


class TemporaryLinkInPlainWords(unittest.TestCase):
    """NC-11: временная ссылка без домена — дважды «сверьте отпечаток SHA-256», «откройте на том компьютере»,
    «300 секунд», «23:06 UTC» и случайный TCP-порт без слова о файрволе хостера. Теперь: минуты, время сервера,
    «браузер предупредит — Подробнее → Перейти на сайт», строка про порт у хостера, отпечаток — один раз."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)
        # Пользователи → admin → «Ключ: QR, файл, ссылка» → «Скачать файлом по ссылке» → профиль 1 → .conf → пауза
        cls.out = cls.stand.keys(['1\n', '1\n', '1\n', '5\n', '1\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n'])
        cls.block = cls.out.split('Что положить в файл?', 1)[1].split('Нажмите Enter', 1)[0] \
            if 'Что положить в файл?' in cls.out else ''
        cls.flat = ' '.join(cls.block.split())

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_minutes_and_server_time_instead_of_seconds_and_utc(self):
        self.assertIn('Ссылка на файл admin-3_1.conf — действует 5 минут:', self.flat, self.out[-3000:])
        self.assertRegex(self.flat, r'Перестанет работать через 5 минут — в \d\d:\d\d по времени сервера\.')
        self.assertNotIn('секунд', self.block)
        self.assertNotIn('UTC', self.block)

    def test_one_line_about_the_browser_and_the_fingerprint_once(self):
        self.assertIn('Так и задумано — нажмите «Подробнее» → «Перейти на сайт».', self.flat, self.block)
        self.assertNotIn('Сверьте отпечаток', self.block)
        self.assertEqual(len(re.findall(r'([0-9A-F]{2}:){31}[0-9A-F]{2}', self.block)), 1, self.block)

    def test_the_port_at_the_hoster_is_named(self):
        m = re.search(r'https://[^:/\s]+:(\d+)/', self.block)
        self.assertIsNotNone(m, self.block)
        self.assertIn('откройте там входящий TCP-порт %s.' % m.group(1), self.flat)

    def test_open_it_on_the_phone_not_on_that_computer(self):
        # NC6-02: ссылка — перенести ключ на СВОЙ телефон за пять минут; без домена о странице для друга ни слова
        flat = ' '.join(self.out.split())
        self.assertIn('Ссылка — чтобы перенести ключ на свой телефон за 5 минут: откройте её на телефоне, и файл '
                      'скачается сразу.', flat)
        self.assertNotIn('на том компьютере', flat)
        self.assertNotIn('Другу удобнее страница подключения', flat)

    def test_the_owner_is_told_what_the_person_will_see(self):
        # NC-12: по ссылке сразу скачивается файл, а после срока у человека «не удаётся подключиться»
        self.assertIn('Что увидит пользователь: браузер сразу скачает файл admin-3_1.conf — его открывают в приложении '
                      'AmneziaWG (импорт из файла).', self.flat, self.block)
        self.assertIn('Когда срок выйдет, у пользователя по ссылке будет «не удаётся подключиться» — нужна снова, '
                      'сделайте новую.', self.flat)

    def test_two_formats_like_the_bot_and_the_panel(self):
        # TAIL-14: по ссылке — только файл (.conf или ключ .vpn), как у бота и панели; QR сканируют с экрана,
        # картинок и архива в этом меню больше нет
        menu = self.out.split('Что положить в файл?', 1)[1].split('Выбор', 1)[0]
        self.assertIn('1) Конфиг AmneziaWG (.conf)', menu, menu)
        self.assertIn('2) Ключ приложения Amnezia (.vpn)', menu, menu)
        self.assertIn('0) « Назад', menu, menu)
        self.assertNotRegex(menu, r'(?m)^\s*[3-9]\)', menu)
        self.assertNotIn('PNG', menu)
        self.assertNotIn('архив', menu)

    def test_active_shares_say_when_in_words(self):
        # TAIL-22: «Пользователи → Активные раздачи» — «истекает через 5 минут», как у страниц подключения,
        # а не «осталось 277 с». Раздачу (срок 5 минут) поднял setUpClass.
        out = self.stand.keys(['1\n', '%d\n' % user_items(self.stand)[2], '0\n', '0\n', '0\n'])
        flat = ' '.join(out.split('Активные раздачи', 1)[-1].split())
        # Профиль раздачи — подписью с портом (решение владельца 15.10), а не голым awg3
        port = self.stand.config()['profiles']['awg3']['port']
        self.assertRegex(flat, r's-[0-9a-f]{4,8} · admin / AmneziaWG 3\.1 · порт %d · conf · истекает через [1-5] '
                               r'минут[уы]? · скачиваний: \d+' % port, flat[-2000:])
        self.assertNotRegex(flat, r'осталось \d+ с')


class DomainQuestionExplainsTheDomain(unittest.TestCase):
    """NC-10: «Домен, который указывает на этот сервер» — ни что такое A-запись, ни где взять домен. Пока домена
    нет, перед вопросом — три строки: что это и где взять, какая A-запись (с адресом сервера), сколько ждать."""

    HELP = ('Домен — адрес вида vpn.example.com. Его покупают у регистратора доменов',
            'добавьте A-запись: имя — поддомен (или @ для самого домена), значение — 203.0.113.10, адрес этого сервера.',
            'Запись начинает работать через несколько минут, иногда через час.')

    def test_settings_domain_item_explains_before_asking(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Настройки → Режим и сайт → Домен → 0; дальше назад до выхода (с паузой или без — нули те же)
            out = stand.keys(['5\n', '1\n', '2\n', '0\n', '0\n', '0\n', '0\n', '0\n'])
            flat = ' '.join(out.split())
            before = flat.split('Домен, который указывает на этот сервер', 1)[0]
            for line in self.HELP:
                self.assertIn(line, before, flat[-3000:])
        finally:
            stand.close()

    def test_the_wizard_domain_step_explains_too(self):
        stand = Stand()
        try:
            # Semi-Pro → справка и вопрос о домене → 0 — назад к режимам → Lite и дальше как обычно
            out = stand.keys(['3\n', '0\n', '4\n', '\n', '\n', 'n\n', '\n', '0\n'])
            flat = ' '.join(out.split())
            before = flat.split('Ваш домен, например vpn.example.com', 1)[0]
            self.assertIn(self.HELP[0], before, flat[:4000])
            self.assertIn('добавьте A-запись', before)
            self.assertTrue(stand.config().get('installed'), out[-2000:])
        finally:
            stand.close()


class PanelAddressNotInTheHeader(unittest.TestCase):
    """NC-15: в режиме с доменом шапка главного меню печатала секретный адрес панели — он уходил с любым
    скриншотом. Теперь в главной шапке адрес замаскирован, как номер карты, целиком — по «п»; в шапке «Режим и сайт» —
    «включена» и где взять адрес; сам адрес — отдельным пунктом «Панель на домене»."""

    SECRET = 'app-Zz9SecretPathForTheNoviceTest0000000000000000'

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.stand.keys(LITE_DEFAULTS)
        cfg = cls.stand.config()
        cfg.update({'mode': 'lite_site', 'domain': 'vpn.example.com'})
        cfg.setdefault('site', {})['cert'] = 'letsencrypt'
        cfg.setdefault('panel', {})['miniapp_path'] = cls.SECRET
        (cls.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        admin = pathlib.Path(cls.stand.env['ADMIN_DIR'])
        admin.mkdir(parents=True, exist_ok=True)
        (admin / 'server.py').write_text('# служба панели стоит\n', encoding='utf-8')
        # Главное меню → Настройки → Режим и сайт → Панель на домене → 0 → назад до выхода
        cls.out = cls.stand.keys(['5\n', '1\n', '7\n', '0\n', '0\n', '0\n', '0\n'], seconds=90)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_main_header_has_no_secret_address(self):
        """Шапка — адрес замаскированным, как номер карты (владелец 04.10): начало и конец секрета, середины нет."""
        header = self.out.split('1) Пользователи', 1)[0]
        self.assertNotIn(self.SECRET, header, header[-2000:])
        self.assertNotIn(self.SECRET[8:-4], header, header[-2000:])
        self.assertIn('vpn.example.com/app-Zz9S••••••0000', header, header[-2000:])
        self.assertIn('показать на 5 мин: F1 (или «п»)', ' '.join(header.split()))

    def _stand(self, extra_env=None):
        stand = Stand()
        stand.keys(LITE_DEFAULTS)
        cfg = stand.config()
        cfg.update({'mode': 'lite_site', 'domain': 'vpn.example.com'})
        cfg.setdefault('site', {})['cert'] = 'letsencrypt'
        cfg.setdefault('panel', {})['miniapp_path'] = self.SECRET
        (stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        admin = pathlib.Path(stand.env['ADMIN_DIR'])
        admin.mkdir(parents=True, exist_ok=True)
        (admin / 'server.py').write_text('# служба панели стоит\n', encoding='utf-8')
        stand.env.update(extra_env or {})
        return stand

    def test_f1_reveals_the_address_in_the_header_for_five_minutes(self):
        """F1 (\\eOP) без Enter — адрес целиком прямо в шапке, «скрою через 5 мин» и предупреждение; отдельного экрана нет
        (владелец 04.10). Другие варианты F1 — PuTTY (\\e[11~) и консоль Linux (\\e[[A) — тоже."""
        for seq in ('\x1bOP', '\x1b[11~', '\x1b[[A'):
            with self.subTest(seq=repr(seq)):
                stand = self._stand()
                try:
                    out = stand.keys([seq, ('until', 'скрою через'), '0\n'], seconds=60)
                    shown = out.split('скрою через', 1)[0].rsplit('Панель', 1)[1]
                    self.assertIn('https://vpn.example.com/%s/' % self.SECRET, shown, out[-2500:])
                    after = out.split('скрою через', 1)[1]
                    self.assertIn('5 мин', after[:20])
                    self.assertIn('не показывайте экран', ' '.join(after.split()))
                    self.assertNotIn('Адрес панели на домене', out)
                finally:
                    stand.close()

    def test_p_is_the_fallback_key(self):
        """«п» — запасная клавиша для терминалов, которые забирают F1 себе: то же самое, адрес в шапке на 5 минут."""
        stand = self._stand()
        try:
            out = stand.keys(['п\n', ('until', 'скрою через'), '0\n'], seconds=60)
            self.assertIn('https://vpn.example.com/%s/' % self.SECRET, out.split('скрою через', 1)[0], out[-2500:])
        finally:
            stand.close()

    def test_the_address_hides_again_after_the_time(self):
        """Срок вышел — на следующей перерисовке шапка снова под маской (срок сокращён до 2 с переменной стенда)."""
        stand = self._stand({'GA_PANEL_REVEAL_SECS': '2'})
        try:
            out = stand.keys(['\x1bOP', ('until', 'скрою через'), ('sleep', 3), '\n', '0\n'], seconds=60)
            last = out.rsplit('Панель', 1)[1]
            self.assertNotIn(self.SECRET, last, last[:1500])
            self.assertIn('app-Zz9S••••••0000', last, last[:1500])
            self.assertIn('показать на 5 мин: F1', ' '.join(last.split()))
        finally:
            stand.close()

    def test_mode_screen_header_has_no_secret_address(self):
        screen = self.out.split('Режим: Semi-Pro', 1)[1].split('1) Сменить режим', 1)[0]
        self.assertNotIn(self.SECRET, screen, screen)
        self.assertIn('включена — адрес по пункту «Панель на домене» ниже', ' '.join(screen.split()))

    def test_the_separate_item_shows_the_address(self):
        item = self.out.split('7) Панель на домене', 1)[1]
        self.assertIn('https://vpn.example.com/%s/' % self.SECRET, item, item[:3000])


class UpstreamScreenDoesNotOfferTheSameVersion(unittest.TestCase):
    """NC-19: «Обновления Amnezia» — «3.1.20260828 → 3.1.20260828 актуально», а «1» всё равно предлагал обновить с
    пересозданием; у 2.0 образ 3.1 без пояснения. Теперь колонка подписана «версия образа ядра», профили — именами
    линеек, актуальный профиль не обновляется, подсказки «номер — обновить» и «state/» нет, когда обновлять нечего."""

    def test_current_profile_is_not_recreated(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            stand.env['GOAMNEZIA_UPSTREAM_FIXTURE_DIR'] = str(ROOT / 'tests' / 'fixtures' / 'upstream_current')
            # Обслуживание → Обновления Amnezia → 1 (awg2, актуален) → пауза → назад → выход
            out = stand.keys(['4\n', '4\n', '1\n', '\n', '0\n', '0\n'], seconds=90)
            flat = ' '.join(out.split())
            self.assertIn('Версия образа ядра: сейчас → свежая', flat, out[-3000:])
            ports = {p: v['port'] for p, v in stand.config()['profiles'].items()}
            self.assertRegex(out, r'1\) AmneziaWG 2\.0 · порт %d\s+3\.1\.20260828 → 3\.1\.20260828\s+актуально\n'
                                  r'\s+2\) AmneziaWG 3\.1 · порт %d\s+3\.1\.20260828 → 3\.1\.20260828\s+актуально\n'
                             % (ports['awg2'], ports['awg3']))
            self.assertIn('поэтому у профиля 2.0 версия образа тоже 3.1', flat)
            self.assertIn('AmneziaWG 2.0 · порт %d уже на свежем образе ядра (3.1.20260828) — обновлять нечего.'
                          % ports['awg2'], flat)
            self.assertNotIn('Обновить профиль', out)
            self.assertNotIn('Номер профиля — обновить его', flat)
            self.assertNotIn('state/', out)
        finally:
            stand.close()


class DiagnosticsInVpnOnlyMode(unittest.TestCase):
    """NC-21: диагностика в «Только VPN» — «⚠ cert-renew юнита нет», абзац про «Чужой», которого на экране нет,
    и подсказка `install.sh --action=quality --json`. Теперь cert-renew «не нужен — сайта с доменом нет», абзац —
    только при чужом таймере, команды моста нет."""

    def test_lite_diagnostics_do_not_scare(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Обслуживание → Диагностика → пауза → назад → выход
            out = stand.keys(['4\n', '7\n', '\n', '0\n', '0\n'], seconds=90)
            timers = out.split('Таймеры', 1)[1]
            row = next((l for l in timers.splitlines() if 'cert-renew' in l), '')
            self.assertIn('не нужен — сайта с доменом нет', row, timers[:2000])
            self.assertNotIn('⚠', row)
            self.assertNotIn('«Чужой»', out)
            self.assertNotIn('--action=', out)
            self.assertNotIn('--json', out)
            # NC6-07: экран кончается итогом словами, а не блоком режимов
            tail = out.split('Значки: ✓ — режим можно выбрать', 1)[-1].split('Нажмите Enter', 1)[0]
            self.assertRegex(' '.join(tail.split()),
                             r'Итог: (проблем нет\.|проблем нет, есть предупреждения — они отмечены ⚠ выше\.|'
                             r'проблем — \d+, они отмечены ✗ выше\.)', tail)
        finally:
            stand.close()


class ImportOnACleanServer(unittest.TestCase):
    """NC-22: «Импорт» на чистом сервере показывал наши контейнеры с жаргоном («подхват записывает… пользователями
    без приватных ключей»), а после «0 — назад» ещё «Нажмите Enter» — следующая набранная цифра терялась. То же
    было у вопроса о домене в «Режим и сайт»."""

    def _stand(self, extra=()):
        stand = Stand()
        stand.keys(LITE_DEFAULTS)
        cfg = stand.config()
        lines = ['amnezia-%s 0.0.0.0:%d->%d/udp' % (p, cfg['profiles'][p]['port'], cfg['profiles'][p]['port'])
                 for p in ('awg2', 'awg3')] + list(extra)
        fx = stand.tmp / 'docker_ps.txt'
        fx.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (stand.tmp / 'cfs').mkdir()
        stand.env['GOAMNEZIA_DOCKER_PS_FIXTURE'] = str(fx)
        stand.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(stand.tmp / 'cfs')
        return stand

    def test_only_our_containers_say_there_is_nothing_to_take_over(self):
        stand = self._stand()
        try:
            out = stand.keys(['4\n', '5\n', '\n', '0\n', '0\n'], seconds=90)
            screen = ' '.join(out.split('Импорт существующей установки Amnezia', 2)[-1].split('Нажмите Enter', 1)[0].split())
            ports = {p: v['port'] for p, v in stand.config()['profiles'].items()}
            self.assertIn('Чужих контейнеров Amnezia на сервере нет — подхватывать нечего. Здесь только профили '
                          'goAmnezia: AmneziaWG 2.0 · порт %d, AmneziaWG 3.1 · порт %d.' % (ports['awg2'], ports['awg3']),
                          screen, screen)
            for jargon in ('без приватных ключей', 'Подхват записывает', 'уже под управлением', 'Номер контейнера'):
                self.assertNotIn(jargon, screen)
        finally:
            stand.close()

    def test_zero_goes_back_without_a_pause_and_the_next_digit_is_not_lost(self):
        stand = self._stand(['amnezia-xray 0.0.0.0:443->443/tcp, 0.0.0.0:443->443/udp'])
        try:
            # Импорт → 0 — назад; сразу 7 — «Диагностика» должна открыться, а не уйти в паузу
            out = stand.keys(['4\n', '5\n', '0\n', '7\n', '\n', '0\n', '0\n'], seconds=90)
            after = out.split('Номер контейнера (0 или Enter — назад):', 1)[1]
            self.assertNotIn('Нажмите Enter', after.split('Выбор:', 1)[0], after[:1500])
            self.assertIn('Полная сводка состояния', after, after[:3000])
        finally:
            stand.close()

    def test_zero_on_the_domain_question_goes_back_without_a_pause(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            # Настройки → Режим и сайт → Домен → 0; сразу 0, 0, 0 — назад до выхода
            out = stand.keys(['5\n', '1\n', '2\n', '0\n', '0\n', '0\n', '0\n'], seconds=90)
            after = out.split('Домен, который указывает на этот сервер', 1)[1]
            before_screen = after.split('Где живёт сайт-заглушка', 1)[0]
            self.assertNotIn('Нажмите Enter', before_screen, before_screen)
            self.assertNotIn('Отменено', before_screen)
        finally:
            stand.close()


class WipeRetryNamesTheFirstFullCopy(unittest.TestCase):
    """NC-25 (блокирующая): «Удалить всё» после обновления с первого раза обрывалось «контейнер amnezia-awg3 не
    снялся» при пустом docker ps -a — голый docker inspect находил одноимённый образ (проверка по отсутствию
    контейнера — tests/test_core_dryrun.sh, раздел NC-25). Повтор при этом называл «копией для восстановления»
    второй архив, уже без профилей, а полный первый в /root молчал. Здесь: снос обрывается на настоящем «не
    снялся» (контейнер-фикстура остаётся), повтор называет полную копию первой попытки и отдельно — вторую."""

    def test_retry_after_an_interrupted_wipe_points_to_the_first_full_copy(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            home = stand.tmp / 'roothome'
            home.mkdir()
            cfs = stand.tmp / 'cfs'
            (cfs / 'amnezia-awg3').mkdir(parents=True)
            (cfs / 'amnezia-awg3' / '.state').write_text('running\n', encoding='utf-8')
            stand.env['GOAMNEZIA_WIPE_HOME'] = str(home)
            stand.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
            wipe = ['5\n', '7\n', '1\n', 'удалить\n']
            out1 = stand.keys(wipe + ['\n', '0\n', '0\n'], seconds=120)
            self.assertIn('Контейнер amnezia-awg3 не снялся', ' '.join(out1.split()), out1[-3000:])
            first = sorted(home.glob('goamnezia_backup_*.tar.gz'))
            self.assertEqual(len(first), 1, list(home.iterdir()))
            # Полная копия первой попытки — с профилями
            got = subprocess.run(['tar', 'xzf', str(first[0]), '-O', '--include=*config.json'] if sys_is_bsd_tar()
                                 else ['tar', 'xzf', str(first[0]), '-O', '--wildcards', '*config.json'],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode('utf-8', 'replace')
            self.assertIn('"awg3"', got, 'первая копия должна быть с профилями')
            # Контейнер снялся — повтор доходит до конца
            shutil.rmtree(str(cfs / 'amnezia-awg3'))
            time.sleep(1.1)       # имя копии — по секундам: вторая не должна совпасть с первой
            out2 = stand.keys(wipe + ['\n', '0\n', '0\n'], seconds=120)
            flat = ' '.join(out2.split())
            self.assertIn('goAmnezia удалён.', flat, out2[-3000:])
            self.assertIn('Полная копия для восстановления — от первой попытки удаления: %s.' % first[0], flat)
            second = [p for p in sorted(home.glob('goamnezia_backup_*.tar.gz')) if p != first[0]]
            self.assertEqual(len(second), 1, list(home.iterdir()))
            self.assertIn('Копия того, что оставалось к этой попытке (уже без профилей): %s.' % second[0], flat)
            self.assertNotIn('Копия для восстановления: %s' % second[0], flat)
        finally:
            stand.close()


def sys_is_bsd_tar():
    """tar на macOS (bsdtar) выбирает файлы из архива по --include, GNU tar — по --wildcards."""
    out = subprocess.run(['tar', '--version'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    return b'bsdtar' in out


class VersionHintKnowsWhetherThereIsAPage(unittest.TestCase):
    """NC-26: в «Только VPN» ключ отсылал за номерами версий «на страницу подключения», которой без домена нет.
    Без домена — «обновите приложение до последней», с доменом и сертификатом — страница."""

    # Пользователи → admin → «Ключ: QR, файл, ссылка» → «Ссылка vpn://» → профиль 1 → пауза → назад до выхода
    KEYS = ['1\n', '1\n', '1\n', '3\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n']

    def test_with_a_domain_the_page_is_named(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            cfg = stand.config()
            cfg.update({'mode': 'lite_site', 'domain': 'vpn.example.com'})
            cfg.setdefault('site', {})['cert'] = 'letsencrypt'
            (stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            out = stand.keys(self.KEYS)
            flat = ' '.join(out.split('Ключ приложения Amnezia', 1)[-1].split())
            self.assertIn('номера есть на странице подключения', flat, out[-3000:])
            self.assertNotIn('просто обновите приложение до последней', flat)
        finally:
            stand.close()

    def test_without_a_domain_just_update(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            out = stand.keys(self.KEYS)
            flat = ' '.join(out.split('Ключ приложения Amnezia', 1)[-1].split())
            self.assertIn('просто обновите приложение до последней', flat, out[-3000:])
            self.assertNotIn('на странице подключения', flat)
        finally:
            stand.close()


class TimesAreRelativeWithTheServerClock(unittest.TestCase):
    """NC-11, остаток: срок страницы, бэкап, сертификат и срок пользователя печатались «2026-09-28 23:17 UTC».
    Теперь сначала — сколько осталось («через 24 ч»), в скобках — часы сервера с пометкой «по времени сервера».
    Здесь — страница подключения на домене (срок пользователя и бэкап — в UserExpiryWording и BackupScreen)."""

    def test_page_expiry_is_relative(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            cfg = stand.config()
            cfg.update({'mode': 'lite_site', 'domain': 'vpn.example.com'})
            cfg.setdefault('site', {})['cert'] = 'letsencrypt'
            (stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            # Пользователи → admin → «Страница подключения» → создать → «24 часа» → пауза → назад до выхода
            out = stand.keys(['1\n', '1\n', '7\n', '1\n', '2\n', '\n', '0\n', '0\n', '0\n', '0\n'], seconds=120)
            if 'нет функции pages_list' in out:
                self.skipTest('ядро страниц подключения не подключено')
            flat = ' '.join(out.split())
            self.assertRegex(flat, r'Перестанет работать через 24 ч \(20\d\d-\d\d-\d\d \d\d:\d\d по времени сервера\)\.',
                             out[-3000:])
            # Строка списка страниц — «истекает через 24 ч (…)»
            self.assertRegex(flat, r'истекает через 24 ч \(20\d\d-\d\d-\d\d \d\d:\d\d по времени сервера\)')
            self.assertNotIn('UTC', out)
        finally:
            stand.close()


class PanelInEveryMode(unittest.TestCase):
    """NC-13: документы обещали «туннель к панели работает всегда», а в «Только VPN» мастер панель не ставил, и
    туннель упирался в Connection reset. Теперь мастер ставит её во всех режимах: без домена — молча, для
    туннеля без пароля, а в конце мастера — строка с командой туннеля. Пароль, которого человек не видел,
    выдаётся, когда панель выходит на домен. Обновление панель молча не ставит."""

    TUNNEL = re.compile(r'ssh -L 1985:127\.0\.0\.1:1985 root@203\.0\.113\.10')

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        cls.out = cls.stand.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_lite_wizard_installs_the_panel_quietly(self):
        self.assertIn('DRY: admin-web/install_admin.sh --auto', self.out, self.out[-3000:])
        self.assertIn('Веб-панель установлена — для SSH-туннеля, без домена и пароля.', self.out)
        # Пароль в «Только VPN» не нужен и не показывается; он выдаётся, когда панель выйдет на домен
        self.assertNotIn('Пароль показан один раз', self.out)
        self.assertIs(self.stand.config().get('panel', {}).get('password_pending'), True)

    def test_the_end_of_the_wizard_names_the_tunnel_command(self):
        tail = self.out.split('Готово. Ключ администратора ниже.', 1)[1]
        self.assertRegex(tail, self.TUNNEL, tail[-3000:])
        flat = ' '.join(tail.split())
        self.assertIn('через SSH-туннель, без пароля', flat)
        self.assertIn('http://127.0.0.1:1985/', flat)

    def test_ssh_on_another_port_goes_into_the_command(self):
        stand = Stand()
        try:
            stand.env['SSH_CONNECTION'] = '198.51.100.7 50000 203.0.113.10 2222'
            out = stand.keys(LITE_DEFAULTS)
            self.assertIn('ssh -p 2222 -L 1985:127.0.0.1:1985 root@', out, out[-3000:])
        finally:
            stand.close()

    def test_the_unseen_password_is_issued_when_the_panel_goes_to_the_domain(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            self.assertIs(stand.config().get('panel', {}).get('password_pending'), True)
            admin = pathlib.Path(stand.env['ADMIN_DIR'])
            admin.mkdir(parents=True, exist_ok=True)
            (admin / 'server.py').write_text('# служба уже стоит\n', encoding='utf-8')
            # Настройки → Режим и сайт → Сменить режим → Semi-Pro (3) → домен → да → шаблон: нет → пауза → назад
            out = stand.keys(['5\n', '1\n', '1\n', '3\n', 'vpn.example.com\n', '1\n', '0\n', '\n', '0\n', '0\n', '0\n'],
                             seconds=120)
            self.assertIn('Пароль при установке не показывали', ' '.join(out.split()), out[-4000:])
            self.assertIn('DRY: admin-web/install_admin.sh --reset-password', out, out[-4000:])
            self.assertIs(stand.config().get('panel', {}).get('password_pending'), False)
        finally:
            stand.close()

    def test_an_update_does_not_install_the_panel(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            cfg = stand.config()
            cfg['version'] = '0.4.6'
            cfg.get('panel', {}).pop('password_pending', None)
            (stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
            out = stand.keys(['\n', '0\n'], seconds=60)
            self.assertNotIn('install_admin.sh', out, out[-3000:])
            self.assertNotIn('Веб-панель установлена', out)
        finally:
            stand.close()


PAGE_URL = r'https://{}/p-[A-Za-z0-9_-]{{43}}/'


def lazy_stand():
    """Стенд Ленивого Pro: сайт и сертификаты — в каталоге стенда, шаблон — из фикстуры (без сети), A-запись любого
    домена — адрес стенда, ожидание DNS — секунды вместо пятнадцати секунд и пяти минут."""
    stand = Stand()
    t = stand.tmp
    stand.env.update({
        'GOAMNEZIA_NGINX_DIR': str(t / 'nginx'), 'GOAMNEZIA_SITE_ROOT': str(t / 'site'),
        'GOAMNEZIA_ACME_ROOT': str(t / 'acme'), 'GOAMNEZIA_LE_DIR': str(t / 'le'),
        'GOAMNEZIA_TEMPLATE_LOCAL_DIR': str(ROOT / 'tests' / 'fixtures' / 'site_min'),
        'GOAMNEZIA_DNS_STUB': '203.0.113.10',
        'GOAMNEZIA_LAZY_DNS_INTERVAL': '1', 'GOAMNEZIA_LAZY_DNS_TIMEOUT': '2',
    })
    return stand


class LazyProWizard(unittest.TestCase):
    """0.6.0, docs/MODES_047.md. F-2: первый экран мастера — четыре режима в порядке договора (1 Ленивый Pro, 2 Pro,
    3 Semi-Pro, 4 Lite). F-1: в Ленивом Pro человек вводит только домен, дальше без вопросов — проверки, сертификат,
    сайт, 2.0 на UDP 443 и 3.1 на случайном порту, панель на домене, 5 ключей со страницами — и сводка: сайт, панель
    с паролем один раз, страницы, QR по номеру, «Привязать бота?». F-3: в Pro пункт «Режим и сайт → 9» ставит на
    UDP 443 другую линию, прежняя уезжает на случайный свободный порт."""

    @classmethod
    def setUpClass(cls):
        cls.stand = lazy_stand()
        # 1 — Ленивый Pro; домен; QR первой страницы; Enter — дальше; n — без бота; Enter — пауза; 0 — выход
        cls.out = cls.stand.keys(['1\n', 'example.com\n', '1\n', '\n', 'n\n', '\n', '0\n'], seconds=300)
        cls.cfg = cls.stand.config()
        cls.users = [u['name'] for u in cls.stand.users().get('users', [])]
        # Настройки → Режим и сайт → 9 Линия на UDP 443 → 2 (3.1) → 1 — перенести → пауза → назад, назад, выход
        cls.out_line = cls.stand.keys(['5\n', '1\n', '9\n', '2\n', '1\n', '\n', '0\n', '0\n', '0\n', '0\n'],
                                      seconds=180)
        cls.cfg_line = cls.stand.config()

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_mode_screen_lists_four_modes_in_contract_order(self):
        items = re.findall(r'^\s+([1-4])\) (Ленивый Pro|Pro|Semi-Pro|Lite)\s', self.out, re.M)
        self.assertEqual(items[:4], [('1', 'Ленивый Pro'), ('2', 'Pro'), ('3', 'Semi-Pro'), ('4', 'Lite')],
                         self.out[:4000])
        self.assertIn('Выбор (Enter — 4, Lite)', self.out)
        self.assertIn('Повторить проверку — 5', self.out)

    def test_only_the_domain_is_asked_before_the_summary(self):
        between = self.out.split('Домен, например', 1)[1].split('Готово: Ленивый Pro', 1)[0]
        self.assertIn('Домен example.com указывает на этот сервер', between, between[-3000:])
        for question in ('[1 — да', 'Выбор', 'Какие профили', 'Порты профилей'):
            self.assertNotIn(question, between, between[-3000:])

    def test_summary_names_site_panel_pages_and_offers_qr_and_bot(self):
        tail = self.out.split('Готово: Ленивый Pro', 1)[1]
        flat = twp_clean(tail)
        self.assertIn('Режим: Pro', flat, tail[:3000])
        self.assertIn('На UDP 443 — AmneziaWG 2.0: её понимает больше всего клиентов', flat)
        self.assertRegex(flat, r'3\.1 — на порту \d+\. Поменять — «Настройки → Режим и сайт → Линия на UDP 443»')
        self.assertIn('Сайт: https://example.com/', flat)
        self.assertRegex(flat, r'Веб-панель: https://example\.com/app-[A-Za-z0-9_-]{86}/')
        # Пароль панели выдаётся один раз, здесь (на стенде — сухая строка сброса пароля)
        self.assertIn('DRY: admin-web/install_admin.sh --reset-password', tail)
        self.assertEqual(len(re.findall(PAGE_URL.format(r'example\.com'), tail)), 5, tail[:4000])
        self.assertIn('QR — номер (1–5)', flat)
        self.assertIn('Страница подключения «', tail)
        self.assertIn('█', tail)
        self.assertIn('Бота можно привязать позже', flat)

    def test_config_and_people_after_the_wizard(self):
        cfg = self.cfg
        self.assertIs(cfg.get('installed'), True, self.out[-3000:])
        self.assertEqual(cfg.get('mode'), 'pro')
        self.assertEqual(cfg.get('domain'), 'example.com')
        self.assertEqual(cfg['profiles']['awg2']['port'], 443)
        self.assertNotEqual(cfg['profiles']['awg3']['port'], 443)
        self.assertIs(cfg.get('panel', {}).get('password_pending'), False)
        # Пятеро с именами 🎲 из lib/names_capitals.json, у каждого обе линии; администратора мастер не заводит
        names = {n['id'] for n in json.loads((ROOT / 'lib' / 'names_capitals.json').read_text(encoding='utf-8'))}
        self.assertEqual(len(self.users), 5, self.users)
        self.assertTrue(set(self.users) <= names, self.users)
        for u in self.stand.users().get('users', []):
            self.assertEqual(sorted(u.get('peers', {})), ['awg2', 'awg3'], u)

    def test_pro_line_item_puts_the_other_line_on_443(self):
        flat = twp_clean(self.out_line)
        # Порт стоит в самой фразе или колонкой — подпись без него (решение владельца 15.10, DUP-LINE-01).
        self.assertIn('Линия на UDP 443: AmneziaWG 2.0\n', self.out_line, self.out_line[-4000:])
        self.assertIn('Рекомендуем 2.0', flat)
        self.assertEqual(self.cfg_line.get('mode'), 'pro')
        self.assertEqual(self.cfg_line['profiles']['awg3']['port'], 443, self.out_line[-3000:])
        moved = self.cfg_line['profiles']['awg2']['port']
        self.assertNotEqual(moved, 443)
        self.assertIn('AmneziaWG 2.0 переехал на порт %s.' % moved, flat)
        self.assertIn('Линия на UDP 443: AmneziaWG 3.1\n', self.out_line)

    def test_lazy_from_mode_and_site_keeps_ports_people_use(self):
        """Установленный сервер: Ленивый Pro — пункт «Сменить режим». Домен с кириллицей и мусором ввода чистится в
        punycode; обе линии уже стоят на своих портах — их не двигаем, встаёт Semi-Pro, и это сказано один раз."""
        stand = lazy_stand()
        try:
            stand.keys(LITE_DEFAULTS)
            before = {p: v['port'] for p, v in stand.config()['profiles'].items()}
            # Настройки → Режим и сайт → Сменить режим → 1 Ленивый Pro → домен → 1 — да → Enter (без QR) → n (бот)
            # → пауза → назад, назад, выход
            out = stand.keys(['5\n', '1\n', '1\n', '1\n', 'https://Пример.РФ./\n', '1\n', '\n', 'n\n', '\n',
                              '0\n', '0\n', '0\n'], seconds=300)
            flat = twp_clean(out)
            cfg = stand.config()
            self.assertIn('1) Ленивый Pro — только домен, остальное сделаю сам', out, out[-4000:])
            self.assertIn('Домен с кириллицей: пример.рф → xn--e1afmkfd.xn--p1ai (punycode)', flat, flat[-4000:])
            self.assertEqual(flat.count('Обе линии AmneziaWG (2.0 и 3.1) уже стоят на своих портах'), 1, flat[-4000:])
            self.assertIn('Итог — режим Semi-Pro, новых ключей со страницами: 5', flat)
            # LZ-06: «[1 — да, 0 — нет]» в длинном вопросе не рвётся посередине — пробелы внутри неразрывные
            self.assertIn('[1\u00a0—\u00a0да,\u00a00\u00a0—\u00a0нет]', out)
            self.assertIn('Готово: Ленивый Pro', out)
            self.assertEqual(cfg.get('mode'), 'lite_site', out[-3000:])
            self.assertEqual(cfg.get('domain'), 'xn--e1afmkfd.xn--p1ai')
            self.assertEqual({p: v['port'] for p, v in cfg['profiles'].items()}, before)
            self.assertEqual(len(re.findall(PAGE_URL.format(r'xn--e1afmkfd\.xn--p1ai'), out)), 5, out[-4000:])
            self.assertEqual(len(stand.users().get('users', [])), 6)   # admin мастера Lite и пятеро Ленивого Pro
        finally:
            stand.close()


class PanelEmergencyKeyItem(unittest.TestCase):
    """SEC50-08 (решение владельца 28.09.2026): «на домене — только пароль или код из бота; аварийный ключ — только
    формировать в SSH полной строкой». Пункт «Настройки → Веб-панель, админы бота, ИИ-агенты → Панель и мини-апп →
    Аварийный ключ» выпускает новый ключ на час и печатает одной строкой команду туннеля и адрес с ключом. Файлы, срок
    и права подробно — tests/test_panel_key_dryrun.sh."""

    def test_item_prints_the_tunnel_command_and_the_address_with_the_key(self):
        stand = Stand()
        try:
            stand.keys(LITE_DEFAULTS)
            admin = pathlib.Path(stand.env['ADMIN_DIR'])
            admin.mkdir(parents=True, exist_ok=True)
            (admin / 'server.py').write_text('# служба уже стоит\n', encoding='utf-8')
            # Настройки (5) → Веб-панель, админы бота, ИИ-агенты (3) → Панель и мини-апп (1) → Аварийный ключ (5) →
            # пауза → назад, назад, назад, выход
            out = stand.keys(['5\n', '3\n', '1\n', '5\n', '\n', '0\n', '0\n', '0\n', '0\n'], seconds=120)
            key = (admin / 'token').read_text(encoding='utf-8').strip()
            self.assertRegex(key, r'^[A-Za-z0-9_-]{43}$', out[-3000:])
            self.assertIn('5) Аварийный ключ: вход через SSH-туннель на 1 час', out)
            lines = [line.strip() for line in out.splitlines()]
            self.assertIn('ssh -L 1985:127.0.0.1:1985 root@203.0.113.10', lines, out[-3000:])
            self.assertIn('http://127.0.0.1:1985/?token=' + key, lines, out[-3000:])
            self.assertLess(lines.index('ssh -L 1985:127.0.0.1:1985 root@203.0.113.10'),
                            lines.index('http://127.0.0.1:1985/?token=' + key))
            flat = twp_clean(out)
            self.assertIn('прежний больше не действует', flat)
            self.assertIn('Ключ живёт 1 час, после входа сгорает и с домена не работает', flat)
            meta = json.loads((admin / 'token.meta').read_text(encoding='utf-8'))
            self.assertEqual(sorted(meta), ['created', 'expires'])
            self.assertEqual(meta['expires'] - meta['created'], 3600)
            for f in ('token', 'token.meta'):
                self.assertEqual((admin / f).stat().st_mode & 0o777, 0o600, f)
            log = pathlib.Path(stand.env['GOAMNEZIA_LOG_FILE'])
            if log.exists():
                self.assertNotIn(key, log.read_text(encoding='utf-8', errors='replace'))
        finally:
            stand.close()


class ProWizardLineChoice(unittest.TestCase):
    """LZ-11: мастер Pro ставил на UDP 443 линию 2.0 молча — экрана выбора, как в «Режим и сайт → Линия на UDP 443»,
    у него не было. Теперь после домена — этот экран: 2.0 первой, «рекомендуем» и одна строка почему; Enter — 2.0.
    Ленивый Pro — без вопросов, как и был (LazyProWizard: между доменом и сводкой вопросов нет)."""

    def test_enter_keeps_2_0_on_443_and_the_screen_says_why(self):
        stand = Stand()
        try:
            # 2 — Pro; домен пусто; Enter — линия (2.0); профили и порты как есть; 0 — шаблон; n — бот
            out = stand.keys(['2\n', '\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
            cfg = stand.config()
            flat = twp_clean(out)
            screen = flat.split('Pro · линия на UDP 443', 1)[1].split('Какие профили поставить?', 1)[0]
            # Профили ещё не стоят — линией, без имени места и порта (решение владельца 15.10)
            self.assertIn('1) AmneziaWG 2.0 — рекомендуем 2) AmneziaWG 3.1 0) « Назад', screen, out[-4000:])
            self.assertLess(screen.index('AmneziaWG 2.0'), screen.index('AmneziaWG 3.1'), '2.0 — первой')
            self.assertIn('Почему 2.0: её понимает больше всего клиентов, включая роутеры', screen)
            self.assertIn('Выбор (Enter — 1, AmneziaWG 2.0; 0 — назад)', screen)
            self.assertEqual(cfg.get('mode'), 'pro', out[-3000:])
            self.assertEqual(cfg['profiles']['awg2']['port'], 443)
            self.assertNotEqual(cfg['profiles']['awg3']['port'], 443)
        finally:
            stand.close()

    def test_3_1_on_443_locks_its_checkbox_and_2_0_gets_its_own_port(self):
        stand = Stand()
        try:
            # 2 — Pro; домен пусто; 2 — 3.1 на 443; «1» на галочке 3.1 — она замкнута; Enter; порты как есть
            out = stand.keys(['2\n', '\n', '2\n', '1\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
            cfg = stand.config()
            self.assertIn('3.1 встанет на UDP 443 — так выбрано шагом «Линия на UDP 443»', twp_clean(out), out[-4000:])
            self.assertEqual(cfg.get('mode'), 'pro', out[-3000:])
            self.assertEqual(cfg['profiles']['awg3']['port'], 443, out[-3000:])
            self.assertNotEqual(cfg['profiles']['awg2']['port'], 443)
            ports = re.findall(r'\n    AmneziaWG 3\.1\s+(\d+)', out)
            self.assertIn('443', ports, 'в сводке портов 3.1 — на 443')
        finally:
            stand.close()

    def test_zero_goes_back_to_the_domain_and_the_choice_can_change(self):
        stand = Stand()
        try:
            # 2 — Pro; домен пусто; 2 — 3.1; 0 на галочках — назад к линии; Enter — теперь 2.0; дальше как есть
            out = stand.keys(['2\n', '\n', '2\n', '0\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
            cfg = stand.config()
            self.assertEqual(out.count('Pro · линия на UDP 443'), 2, out[-4000:])
            self.assertEqual(cfg['profiles']['awg2']['port'], 443, out[-3000:])
            self.assertNotEqual(cfg['profiles']['awg3']['port'], 443)
            # «0» на самом экране линии — назад к домену
            stand2 = Stand()
            try:
                out = stand2.keys(['2\n', '\n', '0\n', '\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'])
                self.assertGreaterEqual(out.count('Ваш домен'), 2, out[-4000:])
                self.assertEqual(stand2.config().get('mode'), 'pro', out[-3000:])
            finally:
                stand2.close()
        finally:
            stand.close()


class SemiProWithoutDomain(unittest.TestCase):
    """LZ-10: Semi-Pro без домена (сайт на запасном сертификате). «Страница подключения» отсылала к «Сменить режим», а
    домен задаётся пунктом «Домен»; «Сменить режим → Pro» домена не спрашивал и ставил Pro без домена. Теперь подсказка
    ведёт в «Домен», а переход в Pro без домена — отказ с той же подсказкой, ничего не меняется."""

    @classmethod
    def setUpClass(cls):
        cls.stand = lazy_stand()
        # 3 — Semi-Pro; домен пусто; профили и порты как есть; 0 — шаблон; n — бот
        cls.install = cls.stand.keys(['3\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'], seconds=240)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_installed_as_semi_pro_without_a_domain(self):
        cfg = self.stand.config()
        self.assertEqual((cfg.get('mode'), cfg.get('domain') or ''), ('lite_site', ''), self.install[-3000:])

    def test_page_hint_leads_to_the_domain_item(self):
        # Пользователи → admin → Страница подключения → создать → назад до выхода
        out = self.stand.keys(['1\n', '1\n', '7\n', '1\n', '0\n', '0\n', '0\n', '0\n', '0\n'])
        if 'нет функции pages_list' in out:
            self.skipTest('ядро страниц подключения не подключено')
        flat = twp_clean(out)
        self.assertIn('Появится домен — появятся и страницы: «Настройки → Режим и сайт → Домен».', flat, out[-3000:])
        self.assertNotIn('появятся и страницы: «Настройки → Режим и сайт → Сменить режим»', flat)

    def test_change_mode_to_pro_without_a_domain_is_refused_with_the_hint(self):
        before = self.stand.config()
        # Настройки → Режим и сайт → Сменить режим → 2 Pro → пауза → назад, назад, выход
        out = self.stand.keys(['5\n', '1\n', '1\n', '2\n', '\n', '0\n', '0\n', '0\n'])
        after = self.stand.config()
        flat = twp_clean(out)
        self.assertIn('Pro без домена не бывает: в Pro ключи ведут на домен. Сначала задайте домен — «Настройки → '
                      'Режим и сайт → Домен»', flat, out[-3000:])
        self.assertIn('Сейчас ничего не изменилось.', flat)
        self.assertNotIn('Pro · линия на UDP 443', flat, 'до выбора линии не доходим')
        self.assertEqual(after.get('mode'), 'lite_site')
        self.assertEqual({p: v.get('port') for p, v in after.get('profiles', {}).items()},
                         {p: v.get('port') for p, v in before.get('profiles', {}).items()})


if __name__ == '__main__':
    unittest.main()

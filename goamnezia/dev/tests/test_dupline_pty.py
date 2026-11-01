# -*- coding: utf-8 -*-
"""DUP-LINE-01, KEY-APP-01/02 — SSH-меню в настоящем терминале (pty), как его видит человек.

Сервер пользователя (15.10, 1.0.11): amnezia-awg2 приложения Amnezia подхвачен (3.1, порт 46567), «Установить» awg3
поставило по месту ещё одну 3.1 (порт 6714). Стенд мастера (tests/test_wizard_pty.Stand, Lite по умолчанию) приводится
к той же раскладке правкой config.json; человек rudneva — с ключом только в приложении (подхвачен, приватного ключа на
сервере нет), anna — со своим ключом на awg3.

Проверяется:
  • шапка главного меню и «Протоколы»: «нет AmneziaWG 2.0 … роутеры Keenetic не подключатся» и куда идти за 2.0;
    в «Протоколах» имена без порта (порт — своей колонкой), заголовок без «Три линейки»;
  • карточка awg3: предупреждение и пункт «11) Переставить на 2.0»; согласие набором порта; итог по шагам; после —
    awg3 стоит 2.0 на том же порту, профиль приложения в awg2 не тронут;
  • карточка rudneva: «Профили: AmneziaWG 3.1 (awg2 · UDP 46567)» и в предупреждении о ключе из приложения — та же
    подпись, а не голое «awg2»;
  • экран «Ключ» у rudneva (ключи только в приложении): нет QR/.conf/vpn:///ссылки — только «Привязать старый ключ» и
    «Перевыпустить ключ на профиле» со строкой, почему (KEY-APP-02).

Запуск: python3 -m unittest tests.test_dupline_pty
"""
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_wizard_pty import Stand, LITE_DEFAULTS, BASH, ROOT  # noqa: E402


class DupLineInTerminal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        st = cls.stand
        st.keys(LITE_DEFAULTS)
        cfg = st.config()
        assert {'awg2', 'awg3'} <= set(cfg.get('profiles', {})), cfg.get('profiles')
        # Раскладка пользователя: awg2 — 3.1 приложения на 46567 (подхвачен), awg3 — своя 3.1 на 6714.
        cfg['profiles']['awg2'].update({'line': '3.1', 'port': 46567, 'imported': True, 'preset': 'imported'})
        cfg['profiles']['awg3'].update({'line': '3.1', 'port': 6714})
        (st.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        users = st.users()
        users.setdefault('users', [])
        users['users'] = [u for u in users['users'] if u.get('name') not in ('rudneva', 'anna')]
        now = '2026-10-15T10:00:00Z'
        users['users'].append({'name': 'rudneva', 'label': '', 'created': now, 'enabled': True, 'quota_bytes': 0,
                               'expires': '', 'traffic': {}, 'imported': True,
                               'peers': {'awg2': {'ip': '10.8.1.7', 'pubkey': 'UFVCS0VZcnVkbmV2YS0wMTIzNDU2Nzg5MDEyMzQ1Njc4OTA=',
                                                  'privkey': '', 'psk': '', 'created': now, 'active': True,
                                                  'imported': True}}})
        (st.dir / 'users.json').write_text(json.dumps(users), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    @staticmethod
    def flat(out):
        return ' '.join(out.split())

    def test_1_header_and_protocols_say_there_is_no_20(self):
        out = self.stand.keys(['3\n', ('until', 'Выбор'), '0\n', '0\n'])
        flat = self.flat(out)
        # F-2: шапка главного меню — одной-двумя строками, без абзаца lines_check.text
        head = self.flat(out[:out.index('1) Пользователи')])
        self.assertIn('Нет AmneziaWG 2.0: старые приложения и роутеры Keenetic на обычной прошивке не подключатся — «Протоколы» → '
                      'AmneziaWG 3.1 · порт 6714 → «Переставить на 2.0».', head, out[-3000:])
        self.assertNotIn('AmneziaVPN до 5.0.1.5', head)
        self.assertNotIn('повторяет линию другого профиля', head)
        # «Протоколы» — полный текст и путь без «Протоколы →»
        self.assertIn('На сервере нет AmneziaWG 2.0: старые версии приложений (AmneziaVPN до 5.0.1.5, DefaultVPN) и '
                      'роутеры Keenetic на обычной прошивке не подключатся ни к одному профилю (3.1 Keenetic понимает пока только '
                      'в dev-прошивке).', flat, out[-3000:])
        self.assertIn('AmneziaWG 3.1 · порт 6714 повторяет линию другого профиля — его можно переставить на '
                      'AmneziaWG 2.0', flat)
        self.assertIn('Переставить: откройте AmneziaWG 3.1 · порт 6714 → «Переставить на 2.0».', flat)
        # «Протоколы»: порт своей колонкой — имя без него; заголовок без «Три линейки»
        self.assertRegex(flat, r'\d\) \S+ AmneziaWG 3\.1 UDP 6714')
        self.assertRegex(flat, r'\d\) \S+ AmneziaWG 3\.1 · из приложения UDP 46567')
        self.assertNotIn('Три линейки', flat)

    def test_2_user_card_names_the_profile_by_line_and_port(self):
        names = [u['name'] for u in self.stand.users()['users']]
        n = names.index('rudneva') + 1
        out = self.stand.keys(['1\n', ('until', 'rudneva'), '%d\n' % n, ('until', 'Профили'), '0\n', '0\n', '0\n'])
        flat = self.flat(out)
        self.assertIn('Профили: AmneziaWG 3.1 · порт 46567 · из приложения', flat, out[-3000:])
        self.assertIn('Ключ на профилях AmneziaWG 3.1 · порт 46567 · из приложения выдан в приложении Amnezia', flat)
        self.assertNotIn('Профили: awg2', flat)

    def test_3_key_screen_of_app_only_person_offers_only_what_works(self):
        names = [u['name'] for u in self.stand.users()['users']]
        n = names.index('rudneva') + 1
        # Пользователи → rudneva → «Ключ» (пункт 1 карточки) → назад
        out = self.stand.keys(['1\n', ('until', 'rudneva'), '%d\n' % n, ('until', 'Профили'), '1\n',
                               ('until', 'Выбор'), '0\n', '0\n', '0\n', '0\n'])
        flat = self.flat(out)
        self.assertIn('QR, файл и ссылки отсюда не выдать: все ключи пользователя выданы в приложении Amnezia.', flat,
                      out[-3000:])
        screen = flat[flat.rindex('QR, файл и ссылки отсюда не выдать'):]
        self.assertIn('1) Привязать старый ключ', screen)
        self.assertIn('2) Перевыпустить ключ на профиле', screen)
        for item in ('QR-код', 'Текст .conf', 'Ссылка vpn://', 'Серия QR', 'Скачать файлом по ссылке'):
            self.assertNotIn(item, screen, item)

    def test_3b_words_on_the_card_are_not_actions(self):
        # P-2: набранное словом (reline, remove) — «Неверный выбор», а не действие в обход номеров
        out = self.stand.keys(['3\n', ('until', 'Выбор'), '1\n', ('until', '11)'), 'reline\n', ('until', 'Неверный выбор'),
                               '\n', ('until', '11)'), 'remove\n', ('until', 'Неверный выбор'), '\n', ('until', '11)'),
                               '0\n', '0\n', '0\n'])
        self.assertEqual(out.count('Неверный выбор'), 2, out[-3000:])
        self.assertNotIn('порт профиля', out)
        cfg = self.stand.config()['profiles']
        self.assertEqual((cfg['awg3']['line'], cfg['awg3']['port']), ('3.1', 6714))

    def test_4_reline_from_the_card(self):
        # «Протоколы» → awg3 (первая строка) → 11) Переставить на 2.0 → ключ остальным (да) → набрать порт профиля → итог
        out = self.stand.keys(['3\n', ('until', 'Выбор'), '1\n', ('until', '11)'), '11\n', ('until', 'вместе с перестановкой'),
                               '1\n', ('until', 'порт профиля'), '6714\n', ('until', 'переставлен'), '\n', '0\n', '0\n', '0\n'],
                              seconds=240)
        flat = self.flat(out)
        # DUPLINE-KEYS-01: вопрос о ключе остальным — до согласия; выдаёт ядро тем же действием, итог — шагом ядра
        self.assertLess(flat.index('вместе с перестановкой'), flat.index('Для подтверждения наберите порт профиля'))
        self.assertIn('Ключи на AmneziaWG 2.0 выданы и остальным пользователям: rudneva.', flat, out[-3000:])
        self.assertNotIn('Выдать им ключ?', flat, 'второго вопроса после итога нет')
        # F-4: итог (ℹ) и шаги (•) — одной шириной со строками ⚠ над ними (GA_UI_W, 76), а не шириной окна (120)
        tail = out[out.index('переставлен на AmneziaWG 2.0'):]
        rows = [ln.rstrip() for ln in tail.splitlines()[:12] if ln.strip()]
        self.assertTrue(any(ln.lstrip().startswith('•') for ln in rows), rows)
        self.assertLessEqual(max(len(ln) for ln in rows), 78, rows)
        rud = [u for u in self.stand.users()['users'] if u['name'] == 'rudneva'][0]
        self.assertTrue((rud['peers'].get('awg3') or {}).get('privkey'), rud['peers'])
        # D-7: пункты починки — следом за 10, «Удалить» — последним, без пропусков и не над «1)»
        self.assertRegex(out, r'10\) [^\n]+\n\s+11\) Переставить на 2\.0\n\s+12\) Удалить\n', out[-3000:])
        self.assertIn('Переставить — пункт 11 «Переставить на 2.0».', flat)
        self.assertNotIn('13)', out)
        # D-5: согласие — набором порта, который пользователь видит в подписи; имени места awg3 нет
        self.assertIn('Для подтверждения наберите порт профиля (6714):', flat)
        self.assertNotIn('«awg3»', flat)
        self.assertIn('AmneziaWG 3.1 · порт 6714 удалю и поставлю заново с линией 2.0 на тот же порт 6714', flat)
        self.assertIn('Профиль приложения Amnezia не трогаю', flat)
        self.assertIn('AmneziaWG 3.1 · порт 6714 переставлен на AmneziaWG 2.0: порт 6714', flat, out[-3000:])
        cfg = self.stand.config()['profiles']
        self.assertEqual((cfg['awg3']['line'], cfg['awg3']['port']), ('2.0', 6714))
        self.assertEqual((cfg['awg2']['line'], cfg['awg2']['port'], cfg['awg2'].get('imported')), ('3.1', 46567, True))

    def test_5_user_scenario_protocols_install_puts_20_into_awg3(self):
        """Сценарий пользователя 15.10: свой awg3 удалён, рядом подхваченная 3.1 приложения в awg2. «Протоколы» —
        свободный awg3 подписан «AmneziaWG 2.0 (awg3)», «Установить» ставит 2.0; профиль приложения цел."""
        st = self.stand
        rm = subprocess.run([BASH, str(ROOT / 'install.sh'), '--action=profile-remove', '--profile=awg3', '--json'],
                            env=st.env, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        self.assertEqual(rm.returncode, 0, rm.stdout[-500:] + rm.stderr[-1500:])
        self.assertNotIn('awg3', st.config()['profiles'])
        # «Протоколы» → awg3 (первая строка) → 1) Установить → порт (Enter — предложенный) → подсеть (Enter) → ключи
        # всем — нет → пауза → назад
        out = st.keys(['3\n', ('until', 'Выбор'), '1\n', ('until', 'Установить'), '1\n', '\n', '\n',
                       ('until', 'awg3'), 'n\n', '\n', '0\n', '0\n', '0\n'], seconds=240)
        flat = self.flat(out)
        self.assertRegex(flat, r'1\) \S+ AmneziaWG 2\.0 — не установлена', out[-4000:])
        self.assertIn('Ставлю профиль AmneziaWG 2.0', flat, out[-3000:])
        self.assertIn('AmneziaWG 2.0 установлен', flat)
        self.assertNotIn('Ставлю профиль AmneziaWG 2.0 — не установлена', flat)
        self.assertNotIn('2.0 занят', flat)
        cfg = st.config()['profiles']
        self.assertEqual(cfg['awg3']['line'], '2.0')
        self.assertEqual((cfg['awg2']['line'], cfg['awg2']['port'], cfg['awg2'].get('imported')), ('3.1', 46567, True))


def menu_dir(stand):
    """install.sh без последней строки (main) рядом со ссылкой на lib — функции меню без запуска меню."""
    import os
    menu = stand.tmp / 'menu'
    if not menu.exists():
        menu.mkdir()
        src = (ROOT / 'install.sh').read_text(encoding='utf-8').rstrip()
        assert src.endswith('main "$@"')
        (menu / 'install.sh').write_text(src[: -len('main "$@"')], encoding='utf-8')
        os.symlink(ROOT / 'lib', menu / 'lib')
    return menu


def pty_run(stand, script, keys, seconds=60):
    """bash в настоящем терминале: install.sh без main (функции меню) и script; клавиши — по мере тишины вывода."""
    import os
    import pty
    import select
    import time
    menu = menu_dir(stand)
    master, slave = pty.openpty()
    proc = subprocess.Popen([BASH, '-c', 'source "$1/install.sh" >/dev/null 2>&1; ' + script, 'pty', str(menu)],
                            stdin=slave, stdout=slave, stderr=slave, env=stand.env, close_fds=True, start_new_session=True)
    os.close(slave)
    out, queue, quiet, deadline = b'', list(keys), 0, time.time() + seconds
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.4)
        if r:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            quiet = 0
            continue
        if proc.poll() is not None:
            break
        quiet += 1
        if queue and quiet >= 2:
            os.write(master, queue.pop(0).encode())
            quiet = 0
        elif not queue and quiet >= 6:
            break
    try:
        proc.kill()
    except OSError:
        pass
    os.close(master)
    import re
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', out.decode('utf-8', 'replace'))


class DuplineNoticeAfterUpdate(unittest.TestCase):
    """Решение владельца 15.10: после обновления продукт сам находит сервер, где своя 3.1 стоит рядом с подхваченной
    3.1 приложения и 2.0 нет (миграция ставит флаг), и в SSH один раз — сразу после «Что нового» — говорит об ошибке
    прошлых версий с «1) Исправить» и «2) Позже»; дальше — строкой в шапке, пока не исправлено."""

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        st = cls.stand
        st.keys(LITE_DEFAULTS)
        cfg = st.config()
        cfg['profiles']['awg2'].update({'line': '3.1', 'port': 46567, 'imported': True, 'preset': 'imported'})
        cfg['profiles']['awg3'].update({'line': '3.1', 'port': 6714})
        (st.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        mark = subprocess.run([BASH, '-c', 'source "$1/install.sh" >/dev/null 2>&1; _migrate_dupline; echo ok',
                               'm', str(menu_dir(st))], env=st.env, capture_output=True, text=True, timeout=120)
        assert 'ok' in mark.stdout, mark.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def test_0_failed_move_says_the_core_words_in_ssh(self):
        """Перестановка сорвалась (сеть Docker при новой линии) — в SSH целиком слова ядра: что вернули и кому ключи
        выданы, а не только «✗ … код 1» (находка проверки 15.10)."""
        script = ('confirm_phrase() { return 0; }; confirm() { return 1; }; _dn=0; '
                  'docker_ensure_network() { _dn=$((_dn + 1)); [ "$_dn" -gt 1 ]; }; '
                  '_profile_reline_interactive awg3 2.0; echo "[end rc=$?]"')
        out = pty_run(self.stand, script, [], seconds=180)
        flat = ' '.join(out.split())
        self.assertIn('[end rc=1]', flat, out[-3000:])
        self.assertIn('вернули прежнюю линию 3.1 на тот же порт и подсеть', flat, out[-3000:])
        self.assertEqual(flat.count('вернули прежнюю линию 3.1 на тот же порт и подсеть'), 1, 'итог — один раз, не дважды')
        self.assertEqual(self.stand.config()['profiles']['awg3']['line'], '3.1')

    def test_0b_early_refusal_is_said_once(self):
        """Ранний отказ (параметры новой линии не проходят проверку — до удаления) печатается в SSH один раз: шаг не
        повторяет первые строки stderr, когда итог ядра скажут целиком (GA_STEP_NO_ERRHEAD)."""
        script = ('confirm_phrase() { return 0; }; confirm() { return 1; }; '
                  'ga_amn_validate_params() { return 1; }; _profile_reline_interactive awg3 2.0; echo "[end rc=$?]"')
        out = pty_run(self.stand, script, [], seconds=120)
        flat = ' '.join(out.split())
        self.assertIn('[end rc=1]', flat, out[-3000:])
        self.assertEqual(flat.count('Не удалось сгенерировать параметры линейки 2.0'), 1, out[-3000:])
        self.assertEqual(self.stand.config()['profiles']['awg3']['line'], '3.1')

    def test_1_once_then_header(self):
        flag = self.stand.config().get('dupline_bug') or {}
        self.assertEqual((flag.get('profile'), flag.get('ssh_shown')), ('awg3', False), flag)
        out = pty_run(self.stand, '_dupline_notice_once; echo "[end]"', ['2\n'])
        flat = ' '.join(out.split())
        self.assertIn('Ошибка прошлых версий: на сервере нет AmneziaWG 2.0', flat, out[-3000:])
        self.assertIn('На сервере нет AmneziaWG 2.0: старые версии приложений (AmneziaVPN до 5.0.1.5, DefaultVPN) не подключатся к 3.1, а '
                      'роутеры Keenetic на обычной прошивке — только к AmneziaWG 1.5 или 1.0, если она стоит (3.1 Keenetic '
                      'понимает пока только в dev-прошивке). Исправление переставит AmneziaWG 3.1 · порт 6714 на 2.0: '
                      'порт и подсеть останутся прежними.', flat)
        self.assertIn('Профиль приложения не тронем.', flat)
        self.assertIn('1) Исправить — переставить AmneziaWG 3.1 · порт 6714 на 2.0', flat)
        self.assertIn('2) Позже', flat)
        self.assertIn('Напоминание останется в шапке главного меню', flat)
        self.assertTrue(self.stand.config()['dupline_bug']['ssh_shown'])
        self.assertEqual(self.stand.config()['profiles']['awg3']['line'], '3.1', '«Позже» ничего не переставляет')
        again = pty_run(self.stand, '_dupline_notice_once; echo "[end]"', [])
        self.assertNotIn('Ошибка прошлых версий', again, 'второй раз экран не показывается')
        head = self.stand.keys(['0\n'])
        hflat = ' '.join(head.split())
        # D-1: в шапке — одна-две строки, а не абзац сообщения
        self.assertIn('Ошибка прошлых версий: нет AmneziaWG 2.0 — «Протоколы» → AmneziaWG 3.1 · порт 6714 → '
                      '«Переставить на 2.0».', hflat, head[-3000:])
        self.assertNotIn('роутеры Keenetic не подключатся', hflat)
        self.assertNotIn('Профиль приложения не тронем', hflat)
        # «Протоколы»: та же строка без «Протоколы →» — пользователь уже там
        prot = self.stand.keys(['3\n', ('until', 'Выбор'), '0\n', '0\n'])
        scr = prot[prot.index('UDP 6714'):]
        pflat = ' '.join(scr[:scr.index('Выбор')].split())
        self.assertIn('Ошибка прошлых версий: нет AmneziaWG 2.0 — откройте AmneziaWG 3.1 · порт 6714 → «Переставить на 2.0».',
                      pflat, prot[-3000:])
        self.assertNotIn('«Протоколы» →', pflat)
        self.assertNotIn('роутеры Keenetic не подключатся', pflat)

    def test_1b_cancelled_fix_says_where_to_fix_later(self):
        """R-4: «1) Исправить», а набор порта отменён — экран больше не покажется, поэтому и тут — где исправить потом."""
        cfg = self.stand.config()
        cfg['dupline_bug']['ssh_shown'] = False
        (self.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        out = pty_run(self.stand, '_dupline_notice_once; echo "[end]"', ['1\n', 'awg3\n', '\n'], seconds=60)
        flat = ' '.join(out.split())
        self.assertIn('Набрано не то — отменено, ничего не сделано.', flat, out[-3000:])
        self.assertNotIn('Отменено.', flat)  # LH-3: об отказе — одна строка
        self.assertIn('Напоминание останется в шапке главного меню; исправить потом — «Протоколы» → AmneziaWG 3.1 · порт 6714 '
                      '→ «Переставить на 2.0».', flat, out[-3000:])
        self.assertEqual(self.stand.config()['profiles']['awg3']['line'], '3.1')

    def test_2_fix_from_the_notice(self):
        cfg = self.stand.config()
        cfg['dupline_bug']['ssh_shown'] = False
        (self.stand.dir / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        # anna — с ключом на awg3: после перестановки он у неё новый, а вопрос — о тех, у кого awg3 не было (masha)
        for name, profs in (('anna', 'awg3'), ('masha', 'awg2')):
            bridge = subprocess.run([BASH, str(ROOT / 'install.sh'), '--action=user-add', '--name=' + name,
                                     '--profiles=' + profs, '--json'], env=self.stand.env, cwd=str(ROOT),
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(bridge.returncode, 0, bridge.stdout[-500:] + bridge.stderr[-1500:])
        # 1) Исправить → ключ остальным — нет → набрать порт профиля → итог → пауза
        out = pty_run(self.stand, '_dupline_notice_once; echo "[end]"', ['1\n', '0\n', '6714\n', '\n'], seconds=240)
        flat = ' '.join(out.split())
        self.assertIn('переставлен на AmneziaWG 2.0: порт 6714', flat, out[-3000:])
        # у каждого пользователя его действие — до согласия (ga_amn_reline_preview), теми же словами, что итог
        before = flat[:flat.index('Для подтверждения наберите порт профиля (6714):')]
        self.assertIn('Кому что сделать после: • Выдавали ключ или файл (admin, anna): прежний больше не подключится', before)
        # D-4, DUPLINE-KEYS-01: у кого ключа на профиле нет, по именам — до согласия, а не после итога; не «нет ни у кого»
        self.assertIn('Ключа на этом профиле нет у пользователей:', before, out[-3000:])
        self.assertNotIn('нет ни у кого', flat)
        ask = before[before.index('Ключа на этом профиле нет у пользователей:'):].split('вместе с перестановкой?')[0]
        self.assertIn('masha', ask)
        self.assertNotIn('anna', ask)
        self.assertIn('Выдать им ключ на AmneziaWG 2.0', ask)
        # отказались — ключа остальным нет, и сказано, где выдать потом; шага ядра «выданы и остальным» нет
        self.assertNotIn('выданы и остальным', flat)
        self.assertIn('Выдать ключ на профиле можно позже', flat)
        masha = [u for u in self.stand.users()['users'] if u['name'] == 'masha'][0]
        self.assertNotIn('awg3', masha.get('peers') or {})
        cfg = self.stand.config()
        self.assertEqual(cfg['profiles']['awg3']['line'], '2.0')
        self.assertEqual((cfg['profiles']['awg2']['line'], cfg['profiles']['awg2'].get('imported')), ('3.1', True))
        self.assertTrue(cfg['dupline_bug'].get('fixed_at'))


class WizardWithAppLine31(unittest.TestCase):
    """Сценарий пользователя в мастере: на сервере amnezia-awg2 приложения Amnezia с 3.1 и людьми (фикстура
    tests/fixtures/import, конфиг 3.1). Подхват — первым экраном; шаг профилей — по линиям: подхваченный awg2 —
    «AmneziaWG 3.1 (awg2) — подхвачен», свободный awg3 — «AmneziaWG 2.0 (awg3)», отмечен и замкнут (2.0 ставится
    всегда). Итог: 2.0 встала в awg3, профиль приложения цел. Раньше галочка 2.0 жила только у места awg2 — «2.0
    подхвачен из приложения», а поставить 2.0 было некуда."""

    def test_wizard_offers_20_in_awg3(self):
        st = Stand()
        try:
            fx = ROOT / 'tests' / 'fixtures' / 'import'
            cfs = st.tmp / 'cfs'
            cfs.mkdir()
            shutil.copytree(fx / 'fs' / 'amnezia-awg2', cfs / 'amnezia-awg2')
            shutil.copy(fx / 'awg2_conf_line31.txt', cfs / 'amnezia-awg2' / 'opt' / 'amnezia' / 'awg' / 'awg0.conf')
            ps = st.tmp / 'docker_ps.txt'
            ps.write_text('amnezia-awg2 0.0.0.0:38123->38123/udp, :::38123->38123/udp\n', encoding='utf-8')
            st.env['GOAMNEZIA_DOCKER_PS_FIXTURE'] = str(ps)
            st.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
            # 1 — подхватить; 4 — Lite; Enter — профили; Enter — порты; n — бот; Enter; 0 — выход
            out = st.keys(['1\n', '4\n', '\n', '\n', 'n\n', '\n', '0\n'], seconds=240)
            cfg = st.config()
        finally:
            st.close()
        flat = ' '.join(out.split())
        self.assertIn('можно подхватить как профиль AmneziaWG 3.1', flat, out[:4000])
        pick = flat[flat.index('[x] 1) AmneziaWG 2.0'):] if '[x] 1) AmneziaWG 2.0' in flat else ''
        self.assertTrue(pick, out[-5000:])
        self.assertIn('AmneziaWG 3.1 · порт 38123 · из приложения', flat)
        self.assertNotRegex(flat, r'awg[23]\)')
        profiles = cfg.get('profiles', {})
        self.assertEqual(profiles.get('awg3', {}).get('line'), '2.0', out[-3000:])
        self.assertEqual((profiles['awg2']['line'], profiles['awg2']['port'], profiles['awg2'].get('preset')),
                         ('3.1', 38123, 'imported'))
        self.assertTrue(cfg.get('installed'))
        self.assertNotRegex(out, r'docker (rm|stop|build)[^\n]*amnezia-awg2\b')

    def test_wizard_pro_puts_20_in_awg3_on_443(self):
        """Тот же сервер, мастер Pro: выбирать линию не из чего (3.1 приложения уже стоит) — на UDP 443 встаёт 2.0, на
        месте awg3; подхваченная 3.1 остаётся на своём порту (находка проверки 15.10: 443 не получал никто)."""
        st = Stand()
        try:
            fx = ROOT / 'tests' / 'fixtures' / 'import'
            cfs = st.tmp / 'cfs'
            cfs.mkdir()
            shutil.copytree(fx / 'fs' / 'amnezia-awg2', cfs / 'amnezia-awg2')
            shutil.copy(fx / 'awg2_conf_line31.txt', cfs / 'amnezia-awg2' / 'opt' / 'amnezia' / 'awg' / 'awg0.conf')
            ps = st.tmp / 'docker_ps.txt'
            ps.write_text('amnezia-awg2 0.0.0.0:38123->38123/udp, :::38123->38123/udp\n', encoding='utf-8')
            st.env['GOAMNEZIA_DOCKER_PS_FIXTURE'] = str(ps)
            st.env['GOAMNEZIA_CONTAINER_FS_DIR'] = str(cfs)
            # 1 — подхватить; 2 — Pro; домен пусто; Enter — профили; Enter — порты; 0 — шаблон; n — бот; Enter; 0
            out = st.keys(['1\n', '2\n', '\n', '\n', '\n', '0\n', 'n\n', '\n', '0\n'], seconds=240)
            cfg = st.config()
        finally:
            st.close()
        profiles = cfg.get('profiles', {})
        self.assertEqual(cfg.get('mode'), 'pro', out[-3000:])
        self.assertEqual((profiles.get('awg3', {}).get('line'), profiles.get('awg3', {}).get('port')), ('2.0', 443), out[-3000:])
        self.assertEqual((profiles['awg2']['line'], profiles['awg2']['port']), ('3.1', 38123))
        self.assertNotIn('3.1 встанет на UDP 443', ' '.join(out.split()))


if __name__ == '__main__':
    unittest.main()

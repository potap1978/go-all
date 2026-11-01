# -*- coding: utf-8 -*-
"""Модуль ядра AmneziaWG, этап Б (docs/KMOD_PLAN.md): диалог «Обслуживание → Ускорение AmneziaWG» в настоящем
терминале (pty), как его видит человек в SSH.

Сухой прогон (GOAMNEZIA_DRY_RUN=1) на стенде мастера (tests/test_wizard_pty.Stand): Lite с профилями по умолчанию.
Команды хоста — заглушки (uname, systemd-detect-virt, mokutil, apt-get, apt-cache, dpkg-query, dkms, modprobe), пути
хоста — во временном каталоге (GOAMNEZIA_KMOD_*): ничего на машине прогона не ставится.

Проверяется:
  • пункт «9) Ускорение AmneziaWG (модуль ядра)» в «Обслуживании», экран: что сейчас, ядро, четыре пункта;
  • отказ проверки хоста — одной фразой с причиной (контейнер LXC), без вопросов и без apt;
  • согласие цифрами по рецепту (вопрос, «1) Да (рекомендуется)…», «2) Нет, оставить программу amneziawg-go»,
    «Выбор [1]:») и «2» — «оставляю программу», ничего не поставлено (ни apt-get install, ни dkms);
  • «Вернуть программу», когда модуля нет, — честно «и так программой», без перезапусков.

Запуск: python3 -m unittest tests.test_kmod_pty
"""
import os
import pathlib
import shutil
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_wizard_pty import Stand, LITE_DEFAULTS, require_exec  # noqa: E402

STUBS = {
    'uname': '#!/bin/sh\ncase "${1:-}" in -r) echo 6.8.0-48-generic ;; -m) echo x86_64 ;; *) echo Linux ;; esac\n',
    'systemd-detect-virt': '#!/bin/sh\nv="${STUB_VIRT:-none}"; echo "$v"; [ "$v" = none ] && exit 1; exit 0\n',
    'mokutil': '#!/bin/sh\nexit 127\n',
    'apt-cache': ('#!/bin/sh\ncase "$1" in policy) printf "%s:\\n  Candidate: 1.0-1\\n" "$2" ;; '
                  'show) shift; for a in "$@"; do case "$a" in -*) ;; *) printf "Package: %s\\nInstalled-Size: 20480\\n\\n" "$a" ;; esac; done ;; esac\n'),
    'apt-get': ('#!/bin/sh\necho "apt-get $*" >>"$STUB_LOG"\nsim=0\nfor a in "$@"; do [ "$a" = -s ] && sim=1; done\n'
                '[ "$sim" = 1 ] || exit 0\nfor a in "$@"; do case "$a" in -*|install|*::*) ;; *) echo "Inst $a (1.0-1 [amd64])" ;; esac; done\n'),
    'dpkg-query': '#!/bin/sh\nexit 1\n',
    'dkms': '#!/bin/sh\necho "dkms $*" >>"$STUB_LOG"\nexit 0\n',
    'modprobe': '#!/bin/sh\necho "modprobe $*" >>"$STUB_LOG"\nexit 1\n',
    'fuser': '#!/bin/sh\nexit 1\n',
}


class KmodScreenInTerminal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.stand = Stand()
        st = cls.stand
        bin_dir = st.tmp / 'kmodbin'
        bin_dir.mkdir()
        for name, text in STUBS.items():
            p = bin_dir / name
            p.write_text(text, encoding='utf-8')
            p.chmod(0o755)
        require_exec(bin_dir / 'uname')
        cls.log = st.tmp / 'stub.log'
        cls.log.write_text('', encoding='utf-8')
        host = st.tmp / 'host'
        for d in ('etc', 'usrsrc', 'libmod/6.8.0-48-generic/build', 'proc', 'sysmodule', 'efivars'):
            (host / d).mkdir(parents=True, exist_ok=True)
        (host / 'proc' / 'modules').write_text('', encoding='utf-8')
        (host / 'proc' / 'version').write_text('Linux version 6.8.0-48-generic (x86_64-linux-gnu-gcc-13 (Ubuntu 13.2.0)) #48\n',
                                               encoding='utf-8')
        df = st.tmp / 'df.txt'
        df.write_text('Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/vda1 20000 10500 9500 53% /\n',
                      encoding='utf-8')
        st.env.update({
            'PATH': str(bin_dir) + os.pathsep + st.env.get('PATH', ''),
            'STUB_LOG': str(cls.log),
            'GOAMNEZIA_KMOD_ETC': str(host / 'etc'), 'GOAMNEZIA_KMOD_SRC_ROOT': str(host / 'usrsrc'),
            'GOAMNEZIA_KMOD_LIBMOD': str(host / 'libmod'), 'GOAMNEZIA_KMOD_PROC': str(host / 'proc'),
            'GOAMNEZIA_SYS_MODULE_DIR': str(host / 'sysmodule'), 'GOAMNEZIA_KMOD_EFIVARS': str(host / 'efivars'),
            'GOAMNEZIA_DF_FIXTURE': str(df),
        })
        st.keys(LITE_DEFAULTS)

    @classmethod
    def tearDownClass(cls):
        cls.stand.close()

    def flat(self, out):
        return ' '.join(out.split())

    def test_1_lxc_refusal_is_one_sentence_and_installs_nothing(self):
        self.log.write_text('', encoding='utf-8')
        self.stand.env['STUB_VIRT'] = 'lxc'
        try:
            # Обслуживание (4) → Ускорение AmneziaWG (9) → Включить (1) → пауза → назад
            out = self.stand.keys(['4\n', '9\n', '1\n', '\n', '0\n', '0\n', '0\n'])
        finally:
            self.stand.env.pop('STUB_VIRT', None)
        flat = self.flat(out)
        self.assertIn('9) Ускорение AmneziaWG (модуль ядра)', out, out[-3000:])
        self.assertIn('1) Включить (перевести на модуль)', out)
        self.assertIn('2) Вернуть программу', out)
        self.assertIn('3) Пересобрать модуль', out)
        self.assertIn('4) Удалить модуль', out)
        self.assertIn('Сейчас: программа amneziawg-go в Docker', flat)
        self.assertIn('Ядро: 6.8.0-48-generic', flat)
        self.assertIn('модуль ядра здесь нельзя: это контейнер LXC, модули ядра в нём не грузятся — '
                      'оставляю программу amneziawg-go', flat, out[-3000:])
        self.assertNotIn('Ускорить AmneziaWG модулем ядра?', flat)
        log = self.log.read_text(encoding='utf-8')
        self.assertNotRegex(log, r'(?m)^dkms ')
        self.assertNotRegex(log, r'apt-get .*install -y')

    def test_2_consent_no_keeps_the_program(self):
        self.log.write_text('', encoding='utf-8')
        out = self.stand.keys(['4\n', '9\n', '1\n', '2\n', '\n', '0\n', '0\n', '0\n'], seconds=150)
        flat = self.flat(out)
        self.assertIn('Ускорить AmneziaWG модулем ядра?', flat, out[-3000:])
        self.assertIn('1) Да (рекомендуется): на том же процессоре — до полутора раз больше скорости', flat)
        self.assertIn('2) Нет, оставить программу amneziawg-go', flat)
        self.assertIn('Выбор [1]:', flat)
        self.assertIn('Поставлю пакеты: dkms, gcc, make, patch, gcc-13', flat)
        self.assertIn('Оставляю программу amneziawg-go — ничего не поставлено.', flat, out[-2000:])
        log = self.log.read_text(encoding='utf-8')
        self.assertNotRegex(log, r'(?m)^dkms ')
        self.assertNotRegex(log, r'apt-get .*install -y')
        self.assertFalse((self.stand.dir / 'state' / 'kmod.json').exists(), 'отказ — и состояния модуля нет')

    def test_3_back_to_program_without_module_says_so(self):
        out = self.stand.keys(['4\n', '9\n', '2\n', '\n', '0\n', '0\n', '0\n'])
        self.assertIn('Профили и так работают программой amneziawg-go.', self.flat(out), out[-2000:])
        self.assertNotIn('DRY: docker restart', out)


if __name__ == '__main__':
    unittest.main()

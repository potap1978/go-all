# -*- coding: utf-8 -*-
"""Часть модуля юнит-тестов — для параллельного прогона tests/run_static.sh.

    python3 tests/unit_shard.py <модуль> <часть> <всего>      (часть — с единицы)

Модуль загружается ровно как у `python3 -m unittest discover -s tests -p '<модуль>.py' -v`
(тот же загрузчик, та же подробность, те же предупреждения), его классы делятся на <всего>
частей, и прогоняется часть <часть>. Нужен модулям, которые одним процессом идут дольше
всего остального набора: pty-мастер (test_wizard_pty) — это минуты на каждом классе.

Деление полное и однозначное: каждая часть строит один и тот же полный список классов и
раскладывает его одним и тем же правилом, поэтому класс попадает ровно в одну часть. Новый
класс не теряется, даже если его нет в таблице WEIGHTS ниже: таблица только выравнивает
части по времени. Первой строкой часть печатает, сколько тестов и классов из скольких ей
досталось, — run_static.sh складывает эти числа по частям и сверяет с полным списком.

Пропуск на уровне модуля (setUpModule бросил SkipTest — например, нет jq) при прогоне одним
процессом считается один раз. Здесь его увидела бы каждая часть, и пропусков стало бы
больше, чем в последовательном прогоне. Поэтому считает его только первая часть, а
остальные молчат; первая часть не бывает пустой (ей достаётся самый тяжёлый класс).
"""
import os
import sys
import unittest

# Примерная длительность классов в секундах (замер 27.09.2026 на Mac, модуль целиком шёл
# 1223 с). Только для равновесия частей: на полноту деления таблица не влияет. Класс,
# которого здесь нет, весит DEFAULT_PER_TEST секунд на тест (в среднем по pty-мастеру).
WEIGHTS = {
    'test_wizard_pty.WizardPorts': 173,
    'test_wizard_pty.AgentsExtendItem': 84,
    'test_wizard_pty.UsersScreen': 66,
    'test_wizard_pty.PromptsNameTheWayOutAndTheCurrentValue': 54,
    'test_wizard_pty.KeyHandover': 54,
    'test_wizard_pty.DnsScreenDoesNotOverwriteSilently': 51,
    'test_wizard_pty.UserExpiryWording': 49,
    'test_wizard_pty.UserCardImportedKey': 64,          # + длинная vpn:// (TAIL-1), 05.10.2026
    'test_wizard_pty.ModeSwitchKeepsItsPromise': 45,
    'test_wizard_pty.KeyForAnotherAddress': 41,
    'test_wizard_pty.WizardChoices': 38,
    'test_wizard_pty.SettingsSayWhatTheyAffect': 38,
    'test_wizard_pty.BotTokenQuestionHasAWayOut': 47,
    'test_wizard_pty.HeaderAddressTable': 29,
    'test_wizard_pty.PagesScreen': 48,
    'test_wizard_pty.AgentsPortQuestion': 25,
    'test_wizard_pty.DisabledUserKey': 23,
    'test_wizard_pty.BackupScreen': 23,
    'test_wizard_pty.AuditLogIsReadableFromTheMenu': 22,
    'test_wizard_pty.ConnectionCheckAfterKey': 25,
    'test_wizard_pty.ProfileInstallSubnet': 21,
    'test_wizard_pty.AnswersWithoutControllingTerminal': 20,
    'test_wizard_pty.PortChangeScreen': 19,
    'test_wizard_pty.BotAdminByMessage': 19,
    'test_wizard_pty.DryRunLeavesHostUnitsAlone': 18,
    'test_wizard_pty.WizardFindsAmneziaContainers': 17,
    'test_wizard_pty.ChangelogAfterBridgeMigration': 16,
    'test_wizard_pty.RestartEverythingNamesWhatItRestarts': 16,
    'test_wizard_pty.MirrorScreen': 16,
    'test_wizard_pty.BackFromSectionsWithoutPause': 16,
    'test_wizard_pty.ProfileRepairItem': 16,
    'test_wizard_pty.S4FixAndMtuStandard': 70,          # S4-FIX и окно MTU, 08.10.2026
    'test_wizard_pty.TrafficScreenLabels': 15,
    'test_wizard_pty.SharesScreen': 15,
    'test_wizard_pty.BrokenUsersFileInTheList': 14,
    'test_wizard_pty.UsersScreenNumbersInOneColumn': 14,
    'test_wizard_pty.LanguageChoiceSurvivesSecondLaunch': 12,
    'test_wizard_pty.WizardLite': 10,
    'test_wizard_pty.WizardWithoutTerminal': 5,
    # Находки критика-новичка NC-… (замер 28.09.2026 на Mac, по классу одним процессом)
    'test_wizard_pty.ImportOnACleanServer': 66,
    'test_wizard_pty.AddUserLikeANovice': 60,
    'test_wizard_pty.PanelInEveryMode': 55,
    'test_wizard_pty.FirstKeyWhenTheQrDoesNotFit': 50,
    'test_wizard_pty.QrSeriesMeasuredBeforeTheHeader': 42,
    'test_wizard_pty.NoviceFirstScreens': 40,
    'test_wizard_pty.DomainQuestionExplainsTheDomain': 31,
    'test_wizard_pty.TemporaryLinkInPlainWords': 22,
    'test_wizard_pty.PanelAddressNotInTheHeader': 18,
    'test_wizard_pty.UpstreamScreenDoesNotOfferTheSameVersion': 17,
    'test_wizard_pty.DiagnosticsInVpnOnlyMode': 16,
    # Повтор критика на 0.5.1 (NC-25…28, остаток NC-11), замер 28.09.2026
    'test_wizard_pty.VersionHintKnowsWhetherThereIsAPage': 40,
    'test_wizard_pty.WipeRetryNamesTheFirstFullCopy': 26,
    'test_wizard_pty.TimesAreRelativeWithTheServerClock': 21,
}
DEFAULT_PER_TEST = 12.0


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def group_key(test):
    cls = type(test)
    return '%s.%s' % (cls.__module__, cls.__qualname__)


def partition(groups, total):
    """[(ключ, [тесты])] → номер части (с нуля) для каждой группы. Долгие — первыми, каждая
    в самую лёгкую на этот момент часть; при равенстве — в часть с меньшим номером."""
    weights = []
    for n, (key, tests) in enumerate(groups):
        w = WEIGHTS.get(key)
        if w is None:
            w = DEFAULT_PER_TEST * len(tests)
        weights.append((-w, n))
    loads = [0.0] * total
    owner = [0] * len(groups)
    for neg_w, n in sorted(weights):
        part = min(range(total), key=lambda p: (loads[p], p))
        owner[n] = part
        loads[part] += -neg_w
    return owner


def is_module_fixture(test):
    return type(test).__name__ == '_ErrorHolder' and str(test).startswith('setUpModule')


def main(argv):
    if len(argv) != 4:
        sys.stderr.write('usage: unit_shard.py <модуль> <часть> <всего>\n')
        return 2
    module, part, total = argv[1], int(argv[2]), int(argv[3])
    if not 1 <= part <= total:
        sys.stderr.write('unit_shard.py: часть %d вне 1..%d\n' % (part, total))
        return 2
    # sys.path — как у `python3 -m unittest discover -s tests` из корня репозитория: первым
    # tests/ (его вставит discover), за ним корень, а не каталог этого файла.
    sys.path[0] = os.getcwd()
    suite = unittest.defaultTestLoader.discover('tests', pattern=module + '.py')
    groups, where = [], {}
    for test in flatten(suite):
        key = group_key(test)
        if key not in where:
            where[key] = len(groups)
            groups.append((key, []))
        groups[where[key]][1].append(test)
    owner = partition(groups, total)
    mine = [g for n, g in enumerate(groups) if owner[n] == part - 1]
    tests = [t for _, ts in mine for t in ts]
    all_tests = sum(len(ts) for _, ts in groups)
    print('unit_shard: %s %d/%d: тестов %d из %d, классов %d из %d'
          % (module, part, total, len(tests), all_tests, len(mine), len(groups)), flush=True)

    first = part == 1

    class ShardResult(unittest.TextTestResult):
        def addSkip(self, test, reason):
            if not first and is_module_fixture(test):
                return          # этот пропуск считает первая часть
            super().addSkip(test, reason)

    # Как unittest.main: без -W предупреждения показываются («default»).
    warnings = None if sys.warnoptions else 'default'
    runner = unittest.TextTestRunner(verbosity=2, resultclass=ShardResult, warnings=warnings)
    result = runner.run(unittest.TestSuite(tests))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))

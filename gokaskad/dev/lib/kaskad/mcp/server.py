"""kaskad-mcp — MCP-сервер Kaskad PRO над ``kaskadctl … --json`` (docs/k3/SPEC-mcp.md).

Model Context Protocol: JSON-RPC 2.0, версия протокола 2025-06-18, транспорт stdio. Агент (Claude Code, Claude
Desktop, Cursor, любой MCP-клиент) запускает ``ssh root@HOST kaskad-mcp``, получает типизированные инструменты и
вызывает их. Каждый вызов превращается в ``kaskadctl <команда> … --json`` отдельным процессом, а JSON-документ его
stdout становится результатом инструмента.

Взято из goamnezia-mcp (goAmnezia Pro) и переложено на Kaskad:

- Всё продуктовое — в actions.json рядом с этим файлом: инструменты (команда kaskadctl, параметры с регексами и
  границами, readonly/destructive/secret, описания RU/EN), команды kaskadctl, которые агенту не публикуются (hidden,
  с причиной), ресурсы и подсказки с шагами. В коде нет ни одного имени инструмента.
- Мост — подпроцесс kaskadctl, а не вызов функций kaskad в этом процессе: каждая команда берёт state.lock сама и
  отпускает его, даже если её убили по таймауту; после обновления Kaskad (lib/current сменился) команды идут уже новым
  кодом, а долгий процесс MCP не держит старые модули; падение или зависание команды не роняет сессию; вывод команд
  (emit, предупреждения) не смешивается с протоколом.
- stdout священен: по stdio клиент читает только JSON-RPC, по сообщению на строку. Логи — в stderr, stdout команды
  перехватывается целиком, sys.stdout после старта подменяется на stderr.
- Разрушительные инструменты требуют confirm поверх подтверждения клиента: confirm равен значению параметра цели
  (по умолчанию первого) или имени инструмента, если цели нет. Это защита от вызова «не того», а подтверждение
  клиента — от вызова «вообще».
- Сводный инструмент (``by_action``, REV-ns-b14 З-3): действие — параметр ``action``, у каждого действия своя команда
  kaskadctl, свои параметры из общих ``params``, свои обязательные (``required``), ``args``, ``readonly`` и
  ``destructive`` (confirm — только у разрушительного действия). Общее описание и общие параметры — в списке один
  раз: каталог короче. Образец для сценариев и вебхуков этапа 3; подтверждение изменения — кодом предпросмотра с
  сервера (вызов без ``code`` ничего не пишет), а не подсказкой клиенту.
- Фраза подтверждения (``"phrase": "<ключ i18n>"`` у параметра-флага): значение должно ТОЧНО совпасть с текстом ключа
  на русском или английском (после NFC и обрезки пробелов по краям), тогда в kaskadctl уходит только сам флаг. Так
  согласие человека (галочка ⚠РФ «скрипт не используется на территории РФ», rules_*) нельзя поставить одной строкой
  true; фразу агент передаёт ту, что подтвердил человек, и сам её не подставляет.
- Секреты: ключ выхода уходит в kaskadctl через stdin (не argv: /proc/<pid>/cmdline читают все), в аудит — только
  длина; QR-коды приходят клиенту картинками, в текстовой копии — заглушка; журнал debug — без значений.
- Только stdlib (Python ≥ 3.8), как всё ядро Kaskad 3.

HTTP-транспорта в этой версии нет (SPEC-mcp §6): доступ — ключ SSH, лучше отдельный с принудительной командой.

Переменные окружения: KASKAD_MCP_CTL, KASKAD_MCP_ACTIONS, KASKAD_MCP_AUDIT, KASKAD_MCP_LANG, KASKAD_MCP_TIMEOUT,
KASKAD_MCP_LOG — см. README (раздел «MCP-сервер»).
"""

import argparse
import base64
import copy
import datetime
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import traceback
import unicodedata

from .. import __version__, paths, util

PRODUCT = "kaskad"
ENV_PREFIX = "KASKAD_MCP"
DEFAULT_AUDIT_LOG = "/var/log/kaskad-mcp.log"

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_TIMEOUT = 300.0                    # секунд на одну команду, если у инструмента нет своего timeout
KILL_GRACE = 3.0                           # секунд между SIGTERM и SIGKILL по таймауту
AUDIT_LINE_MAX = 4096

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RESOURCE_NOT_FOUND = -32002

# Коды выхода kaskadctl (kaskad.errors) словами: агенту легче решить, что делать.
CODE_NAMES = {1: "error", 2: "usage", 3: "conflict", 4: "rolled_back", 5: "unsupported", 6: "locked"}

SENSITIVE_PARAM_RE = re.compile(r"(token|secret|pass|privkey|psk|apikey|api_key)", re.IGNORECASE)
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

T = {
    "err_args_object": {"ru": "arguments должен быть объектом", "en": "arguments must be an object"},
    "err_unknown_tool": {"ru": "Неизвестный инструмент: {name}", "en": "Unknown tool: {name}"},
    "err_unknown_params": {"ru": "Неизвестные параметры: {names}. Допустимые: {allowed}",
                           "en": "Unknown parameters: {names}. Allowed: {allowed}"},
    "err_missing": {"ru": "Не хватает обязательных параметров: {names}", "en": "Missing required parameters: {names}"},
    "err_type": {"ru": "Параметр {name}: ожидается {type}", "en": "Parameter {name}: expected {type}"},
    "err_control": {"ru": "Параметр {name}: управляющие символы и переводы строк недопустимы",
                    "en": "Parameter {name}: control characters and line breaks are not allowed"},
    "err_dash": {"ru": "Параметр {name}: значение не может начинаться с «-»",
                 "en": "Parameter {name}: the value must not start with '-'"},
    "err_pattern": {"ru": "Параметр {name}: значение не подходит под шаблон {pattern}",
                    "en": "Parameter {name}: value does not match pattern {pattern}"},
    "err_pattern_broken": {"ru": "Параметр {name}: шаблон в actions.json не поддерживается валидатором",
                           "en": "Parameter {name}: pattern in actions.json is not supported by the validator"},
    "err_enum": {"ru": "Параметр {name}: допустимые значения: {values}", "en": "Parameter {name}: allowed values: {values}"},
    "err_action_tool": {"ru": ". Это действие — отдельный инструмент {tool}", "en": ". This action is the tool {tool}"},
    "err_min": {"ru": "Параметр {name}: минимум {limit}", "en": "Parameter {name}: minimum is {limit}"},
    "err_max": {"ru": "Параметр {name}: максимум {limit}", "en": "Parameter {name}: maximum is {limit}"},
    "err_confirm": {"ru": "confirm не совпадает со значением параметра «{name}» — разрушительное действие отклонено",
                    "en": "confirm does not match the value of parameter '{name}' — destructive action rejected"},
    "err_confirm_toolname": {"ru": "confirm должен повторять имя инструмента «{name}»",
                             "en": "confirm must repeat the tool name '{name}'"},
    "err_unknown_prompt": {"ru": "Неизвестная подсказка: {name}", "en": "Unknown prompt: {name}"},
    "err_prompt_args": {"ru": "Не хватает обязательных аргументов подсказки: {names}",
                        "en": "Missing required prompt arguments: {names}"},
    "err_resource_file": {"ru": "Файл ресурса {uri} отсутствует на сервере: {path}",
                          "en": "The file behind resource {uri} is missing on the server: {path}"},
    "progress": {"ru": "{tool}: идёт {seconds} с", "en": "{tool}: running for {seconds} s"},
    "danger_desc": {"ru": "Подтверждение флага «{name}» (потеря данных): повторите его имя «{name}» — только после "
                          "явного согласия человека именно на это",
                    "en": "Confirmation of the flag '{name}' (data loss): repeat its name '{name}' — only after the human's "
                          "explicit consent to exactly this"},
    "err_phrase": {"ru": "Параметр {name}: это не фраза, которую подтвердил человек. Покажите человеку блок ⚠РФ "
                         "(организации — rules_status → rf.orgs) и передайте его подтверждение дословно: «{ru}» "
                         "(или «{en}»). Сами фразу не подставляйте — отклонено",
                   "en": "Parameter {name}: this is not the phrase the human confirmed. Show the human the ⚠RU block "
                         "(the organizations — rules_status → rf.orgs) and pass their confirmation word for word: "
                         "'{en}' (or '{ru}'). Never fill the phrase in yourself — rejected"},
    # ТЗ rf-mark §5.3 «отказ с текстом блока» (REV-s1-r67 З-2): блок — дословно, его же в data.block, коды — data.orgs
    "err_phrase_block": {"ru": "Параметр {name}: это не фраза, которую подтвердил человек. Покажите человеку блок "
                               "дословно: «{block}» — и передайте его подтверждение дословно: «{ru}» (или «{en}»), "
                               "а в orgs — data.orgs. Сами фразу не подставляйте — отклонено",
                         "en": "Parameter {name}: this is not the phrase the human confirmed. Show the human the block "
                               "word for word: '{block}' — and pass their confirmation word for word: '{en}' (or "
                               "'{ru}'), and in orgs — data.orgs. Never fill the phrase in yourself — rejected"},
    # SPEC-vless-tls-default §4.2 (З-4): the phrase of the consent to Reality — its own block, no ⚠РФ, no orgs
    "err_phrase_vless": {"ru": "Параметр {name}: это не фраза, которую подтвердил человек. Покажите человеку текст "
                               "рисков Reality (vless_show → reality_risks) и передайте его подтверждение дословно: "
                               "«{ru}» (или «{en}»), а в risks и site — reality_risks.ver и reality_risks.site. Сами "
                               "фразу не подставляйте — отклонено",
                         "en": "Parameter {name}: this is not the phrase the human confirmed. Show the human the text of "
                               "the risks of Reality (vless_show → reality_risks) and pass their confirmation word for "
                               "word: '{en}' (or '{ru}'), and in risks and site — reality_risks.ver and "
                               "reality_risks.site. Never fill the phrase in yourself — rejected"},
    "err_phrase_vless_block": {"ru": "Параметр {name}: это не фраза, которую подтвердил человек. Покажите человеку "
                                     "блок рисков Reality дословно: «{block}» — и передайте его подтверждение дословно: "
                                     "«{ru}» (или «{en}»), а в risks и site — data.risks и data.site. Сами фразу не "
                                     "подставляйте — отклонено",
                               "en": "Parameter {name}: this is not the phrase the human confirmed. Show the human the "
                                     "block of the risks of Reality word for word: '{block}' — and pass their "
                                     "confirmation word for word: '{en}' (or '{ru}'), and in risks and site — data.risks "
                                     "and data.site. Never fill the phrase in yourself — rejected"},
    "err_danger": {"ru": "Флаг «{name}» удаляет данные: нужен confirm_{name} = «{name}» — отклонено",
                   "en": "The flag '{name}' deletes data: confirm_{name} = '{name}' is required — rejected"},
    "one_of_note": {"ru": "Ровно один из: {names}.", "en": "Exactly one of: {names}."},
    "at_most_one_note": {"ru": "Не больше одного из: {names}.", "en": "At most one of: {names}."},
    "err_one_of": {"ru": "Нужен ровно один из параметров: {names}", "en": "Exactly one of these parameters is needed: {names}"},
    "err_at_most_one": {"ru": "Можно не больше одного из параметров: {names}", "en": "At most one of these parameters: {names}"},
    "err_audit": {"ru": "Аудит {path} недоступен — изменения через MCP запрещены, пока он не пишется (чтение работает)",
                  "en": "The audit log {path} is unavailable — changes through MCP are refused until it is writable "
                        "(reads work)"},
    "confirm_desc": {"ru": "Необратимо — подтверждение: повторите значение «{name}»",
                     "en": "Irreversible — the confirmation: repeat the value of '{name}'"},
    "confirm_desc_toolname": {"ru": "Необратимо — подтверждение: повторите имя инструмента",
                              "en": "Irreversible — the confirmation: repeat the tool name"},
    "destructive_note": {"ru": "Необратимо: нужен параметр confirm = значение «{name}».",
                         "en": "Irreversible: requires confirm = value of '{name}'."},
    "destructive_note_tool": {"ru": "Необратимо: нужен параметр confirm = «{name}» (имя инструмента).",
                              "en": "Irreversible: requires confirm = '{name}' (the tool name)."},
    "actions_note": {"ru": "action (* — обязательный): {list}.", "en": "action (* — required): {list}."},
    "destructive_note_action": {"ru": "action={actions} необратимо: нужен confirm = значение «{name}».",
                                "en": "action={actions} is irreversible: requires confirm = value of '{name}'."},
    "destructive_note_action_tool": {"ru": "action={actions} необратимо: нужен confirm = «{name}» (имя инструмента).",
                                     "en": "action={actions} is irreversible: requires confirm = '{name}' (the tool "
                                           "name)."},
    "err_action_params": {"ru": "Параметры {names} — не для action={action}. Для него: {allowed}",
                          "en": "Parameters {names} are not for action={action}. For it: {allowed}"},
    "png_placeholder": {"ru": "<PNG {size} байт: см. изображение №{n}>", "en": "<PNG {size} bytes: see image #{n}>"},
    "transport_cannot_run": {"ru": "Не удалось запустить kaskadctl ({bin}): {error}",
                             "en": "Cannot run kaskadctl ({bin}): {error}"},
    "transport_no_json": {"ru": "kaskadctl завершился с кодом {rc}, но не вернул JSON",
                          "en": "kaskadctl exited with code {rc} but returned no JSON"},
    "timeout": {"ru": "kaskadctl не ответил за {seconds} с и был остановлен",
                "en": "kaskadctl did not answer within {seconds} s and was stopped"},
    "timeout_running": {"ru": "Команда идёт дольше {seconds} с и НЕ остановлена (внутри может работать apt или сборка): "
                              "она закончится сама. Не повторяй её — через пару минут проверь результат чтением.",
                        "en": "The command runs longer than {seconds} s and was NOT stopped (apt or a build may run "
                              "inside): it will finish by itself. Do not repeat it — check the result by reading in a "
                              "few minutes."},
    "usage_no_json": {"ru": "kaskadctl не принял параметры (сочетание не поддерживается) — подробности в stderr_tail",
                      "en": "kaskadctl rejected the parameters (unsupported combination) — see stderr_tail"},
    "prompt_args": {"ru": "Аргументы", "en": "Arguments"},
    "prompt_unset": {"ru": "<не задано — спроси человека>", "en": "<not set — ask the human>"},
    "prompt_steps": {"ru": "Порядок действий", "en": "Steps"},
    "prompt_footer": {"ru": "Перед каждым изменяющим вызовом скажи человеку, что именно будет сделано. Разрушительные "
                            "инструменты ({destructive}) необратимы: только после явного согласия человека и с confirm.",
                      "en": "Before every mutating call tell the human what exactly will be done. Destructive tools "
                            "({destructive}) are irreversible: only after the human's explicit consent and with confirm."},
}


# ---------------------------------------------------------------------------------------------
# Мелкие помощники
# ---------------------------------------------------------------------------------------------

def env_name(suffix):
    return ENV_PREFIX + "_" + suffix


def pick(d, lang):
    """Строка на нужном языке из {"ru": …, "en": …}; lang → ru → en → что есть."""
    if isinstance(d, str):
        return d
    if not isinstance(d, dict):
        return ""
    for key in (lang, "ru", "en"):
        if d.get(key):
            return str(d[key])
    for value in d.values():
        if value:
            return str(value)
    return ""


def msg(lang, key, **kw):
    return pick(T.get(key, {}), lang).format(**kw)


# 3.3.6 (REV-336-awgaddr-2 П-2, SEC-336-awgaddr-2 П-2): невидимые знаки-теги (U+E0000–E007F) — только во флаге области
# Англии, Шотландии и Уэльса (U+1F3F4, теги «gbeng» / «gbsct» / «gbwls», U+E007F — других флагов-эмодзи из тегов нет);
# любые другие теги после U+1F3F4 — чёрный флаг со спрятанными буквами (REV-336-awgaddr-3 П-2); в другом месте они прячут текст, который человек не видит, а агент
# читает (имена выходов из чужих ссылок и подписок). То же правило — egress_parse _TERM_TAGS и gw/exits.py _TAGS. Селекторы вариантов (U+FE00–FE0F, U+E0100–E01EF) так же
# невидимы и несут скрытые байты (REV-336-heal-sec З-2): остаётся один U+FE0E/U+FE0F сразу после видимого знака (❤️, 1️⃣); не после ZWJ, ZWNJ и других
# невидимых знаков, а CGJ, монгольские селекторы и заполнители хангыля — прочь сами (REV-336-heal-2 П-2).
_TAGS = re.compile("(\U0001f3f4\U000e0067\U000e0062(?:\U000e0065\U000e006e\U000e0067|\U000e0073\U000e0063\U000e0074|\U000e0077\U000e006c\U000e0073)\U000e007f)|[\U000e0000-\U000e007f\ufe00-\ufe0d\U000e0100-\U000e01ef\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180d\u180f\u3164\uffa0\U0001d173-\U0001d17a]|(?<![^\x00-\x20\x7f-\xa0\ufe00-\ufe0f\U000e0000-\U000e01ef\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u2060-\u206f\u3164\ufeff\uffa0\U0001d173-\U0001d17a])[\ufe0e\ufe0f]")


def no_tags(text):
    """Строка без знаков-тегов вне флага."""
    return _TAGS.sub(lambda m: m.group(1) or "", text)


def clip_text(value, limit):
    """Без управляющих символов и не длиннее limit — для всего, что пишется в лог и аудит."""
    text = "".join(ch for ch in str(value if value is not None else "") if ch >= " " and ch != "\x7f")
    return text if len(text) <= limit else text[:limit] + "…"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fill_placeholders(text, values, unset="<{key}>"):
    def repl(match):
        key = match.group(1)
        value = values.get(key)
        return str(value) if value not in (None, "") else unset.format(key=key)
    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, text)


class Log(object):
    """Логи только в stderr. Уровни: debug, info (по умолчанию), warn, error, none."""

    LEVELS = {"debug": 10, "info": 20, "warn": 30, "warning": 30, "error": 40, "none": 100, "quiet": 100, "off": 100}

    def __init__(self, level_name, stream):
        self.level = self.LEVELS.get(str(level_name or "info").lower(), 20)
        self.stream = stream
        self.tag = PRODUCT + "-mcp[" + str(os.getpid()) + "]"

    def _emit(self, level, level_name, text):
        if level < self.level:
            return
        try:
            self.stream.write("%s %s %s: %s\n" % (utc_now(), self.tag, level_name, text))
            self.stream.flush()
        except Exception:  # noqa: BLE001 — stderr закрыт: терять лог не страшно, падать нельзя
            pass

    def debug(self, text):
        self._emit(10, "DEBUG", text)

    def info(self, text):
        self._emit(20, "INFO", text)

    def warn(self, text):
        self._emit(30, "WARN", text)

    def error(self, text):
        self._emit(40, "ERROR", text)


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------------------------

def default_ctl():
    """Как запускать kaskadctl: обёртка /opt/kaskad/bin/kaskadctl, а без неё (тесты, разработка) — этот же пакет
    kaskad тем же способом, что и обёртка: python3 -I, путь к коду задаём сами."""
    wrapper = paths.kaskadctl()
    if os.access(wrapper, os.X_OK):
        return [wrapper]
    lib = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    return [sys.executable, "-I", "-c", "import runpy, sys; sys.path.insert(0, %r); "
            "runpy.run_module('kaskad', run_name='__main__', alter_sys=True)" % lib]


class Settings(object):
    def __init__(self, environ, script_dir):
        explicit = environ.get(env_name("CTL"))
        self.ctl = shlex.split(explicit) if explicit else default_ctl()
        self.actions_json = environ.get(env_name("ACTIONS")) or os.path.join(script_dir, "actions.json")
        self.audit_log = environ.get(env_name("AUDIT")) or paths.P(DEFAULT_AUDIT_LOG)
        self.log_level = environ.get(env_name("LOG")) or "info"
        self.lang = (str(environ.get(env_name("LANG")) or "ru").strip().lower()[:2]) or "ru"
        self.timeout = self._parse_timeout(environ.get(env_name("TIMEOUT")))
        self.version = __version__
        self.server_name = PRODUCT

    @staticmethod
    def _parse_timeout(raw):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT
        return value if value > 0 else DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------------------------
# Каталог: инструменты, ресурсы и подсказки из actions.json
# ---------------------------------------------------------------------------------------------

class ParamDef(object):
    """Один параметр инструмента: тип из param_types + где он в командной строке (pos | flag | stdin)."""

    def __init__(self, name, ptype, pdef, log):
        self.name = name
        self.type = str(ptype.get("type") or "string")
        self.pattern = ptype.get("pattern")
        self.enum = ptype.get("enum")
        self.minimum = ptype.get("minimum")
        self.maximum = ptype.get("maximum")
        self.min_length = ptype.get("minLength")
        self.max_length = ptype.get("maxLength")
        self.description = ptype.get("description")
        self.ref = pdef.get("ref", name)
        self.required = bool(pdef.get("required"))
        self.default = pdef.get("default")
        self.pos = bool(pdef.get("pos"))
        self.flag = pdef.get("flag")
        self.stdin = bool(pdef.get("stdin"))
        self.split = pdef.get("split")
        # danger: флаг с потерей данных (with_devices, with_users) — при true нужен свой confirm_<имя> = имя флага
        self.danger = bool(pdef.get("danger"))
        # phrase: ключ i18n — значение параметра должно точно совпасть с его текстом (ru или en); в kaskadctl уходит
        # только флаг (store_true), сама фраза — нет
        self.phrase = pdef.get("phrase")
        # phrase_block: which block a wrong phrase is answered with — "rf" (⚠РФ, by default) or "vless" (the risks of
        # Reality, SPEC-vless-tls-default §4.2 З-4)
        self.phrase_block = pdef.get("phrase_block") or "rf"
        if self.phrase_block not in ("rf", "vless"):
            log.error("actions.json: phrase_block параметра %s — rf или vless" % name)
        self.regex = None
        self.regex_broken = False
        if self.pattern:
            try:
                self.regex = re.compile(self.pattern)
            except re.error as exc:
                # Без валидации нельзя — kaskadctl работает от root: такой параметр отклоняется, проблема видна в --check.
                self.regex_broken = True
                log.error("actions.json: шаблон параметра %s не компилируется: %s" % (name, exc))
        where = sum(1 for x in (self.pos, bool(self.flag), self.stdin) if x)
        if where != 1:
            log.error("actions.json: параметр %s — нужен ровно один из pos, flag, stdin" % name)
        if self.flag and not str(self.flag).startswith("--"):
            log.error("actions.json: ключ параметра %s должен начинаться с --" % name)
        if self.phrase is not None:
            if not self.flag or self.type != "string" or not all(phrase_texts(self.phrase)):
                log.error("actions.json: phrase параметра %s — нужен флаг, тип string и ключ i18n с текстом ru и en"
                          % name)

    def phrase_ok(self, value):
        """Значение — дословно текст ключа ``phrase`` на одном из языков (NFC, без пробелов по краям)."""
        got = _nfc(value)
        return bool(got) and got in [t for t in phrase_texts(self.phrase) if t]

    def json_schema(self, lang="ru"):
        schema = {"type": self.type}
        for key, value in (("pattern", self.pattern), ("minimum", self.minimum), ("maximum", self.maximum),
                           ("minLength", self.min_length), ("maxLength", self.max_length), ("default", self.default)):
            if value is not None:
                schema[key] = value
        if self.enum is not None:
            schema["enum"] = list(self.enum)
        desc = pick(self.description, lang)       # фразы в схеме нет (SEC-s1-r67 №2): она — в отказе
        if desc:
            schema["description"] = desc
        return schema


def _nfc(text):
    return unicodedata.normalize("NFC", text).strip() if isinstance(text, str) else ""


def phrase_texts(key):
    """(ru, en) — тексты ключа i18n фразы подтверждения; нет ключа — пустые строки (параметр тогда ничего не пустит)."""
    from ..i18n import has, tr
    return tuple(_nfc(tr(key, lang)) if has(key, lang) else "" for lang in ("ru", "en"))


def rf_block(lang):
    """{"block", "orgs", "consent"} ⚠РФ сейчас — как в отказе ядра (``rfgate.need_data``): чтение состояния и пакета,
    без процесса; не прочиталось (нет прав, нет файлов) — None, отказ тогда без блока."""
    try:
        from ..rules import rfgate
        nd = rfgate.need_data(rfgate.block_orgs(), lang)
        return {"block": str(nd["block"]), "orgs": [str(o) for o in nd["orgs"]], "consent": str(nd["consent"])}
    except Exception:                       # noqa: BLE001 — подсказка, не проверка: отказ остаётся отказом
        return None


def vless_block(lang, site=None):
    """{"block", "risks", "site"} — the text of the risks of Reality now for the site of the call (З-3: the site the
    human chose), as the core gives it (``front.risks_data``): a read of state, no process; not read — None."""
    try:
        from .. import state
        from ..site import front
        rd = front.risks_data(state.load(), lang, site=site or None)
        return {"block": str(rd["text"]), "risks": str(rd["ver"]), "site": rd["site"]}
    except Exception:                       # noqa: BLE001 — a hint, not a check: the refusal stays a refusal
        return None


def _sentence(text, more):
    """«описание. Ещё фраза.» — между частями точка и пробел (стенд T-M6, F13: «…навсегда) Необратимо»)."""
    text = (text or "").rstrip()
    if text and not text.endswith((".", "!", "?", "…")):
        text += "."
    return (text + " " + more) if text else more


class Variant(object):
    """Одно действие инструмента: команда kaskadctl, постоянные ключи, свои параметры (обязательность — своя), чтение
    или изменение, разрушительное ли (и чей confirm). У обычного инструмента действие одно — он сам (name None)."""

    def __init__(self, name, cmd, static_args, readonly, destructive, params, confirm_param, one_of, at_most_one):
        self.name = name
        self.cmd = cmd
        self.static_args = static_args
        self.readonly = readonly
        self.destructive = destructive
        self.params = params
        self.confirm_param = confirm_param
        self.one_of = one_of
        self.at_most_one = at_most_one
        self.stdin_param = next((p for p in params if p.stdin), None)

    @classmethod
    def of(cls, tool, name, vdef, log):
        """Действие ``name`` сводного инструмента ``tool`` из ``by_action``: параметры — имена общих ``params``."""
        where = "%s action=%s" % (tool.name, name)
        byname = dict((p.name, p) for p in tool.params)
        req = [str(x) for x in vdef.get("required") or []]
        params = []
        for pname in vdef.get("params") or []:
            p = byname.get(pname)
            if p is None:
                log.error("actions.json: %s — неизвестный параметр %s" % (where, pname))
                continue
            q = copy.copy(p)
            q.required = pname in req
            params.append(q)
        for pname in req:
            if pname not in [p.name for p in params]:
                log.error("actions.json: %s — обязательный %s не в его params" % (where, pname))
        cmd = [str(x) for x in vdef.get("cmd") or []]
        if not cmd:
            log.error("actions.json: у %s нет cmd" % where)
        destructive = bool(vdef.get("destructive"))
        confirm = vdef.get("confirm")
        confirm_param = None
        if destructive and confirm != "@tool" and params:
            confirm_param = confirm or params[0].name
            if confirm_param not in [p.name for p in params]:
                log.error("actions.json: confirm %s — неизвестный параметр %s" % (where, confirm_param))
        names = set(p.name for p in params)
        return cls(name, cmd, [str(x) for x in vdef.get("args") or []], bool(vdef.get("readonly")), destructive,
                   params, confirm_param, [g for g in tool.one_of if set(g) <= names],
                   [g for g in tool.at_most_one if set(g) <= names])


class ToolDef(object):
    def __init__(self, name, adef, param_types, log):
        self.name = name
        self.cmd = [str(x) for x in adef.get("cmd") or []]
        self.static_args = [str(x) for x in adef.get("args") or []]
        self.readonly = bool(adef.get("readonly"))
        self.destructive = bool(adef.get("destructive"))
        # ask_human: клиент спрашивает человека перед вызовом (destructiveHint), confirm не нужен — согласие ⚠РФ
        # (SEC-s1-r67 №2, решение координатора: фраза доступна агенту, решает человек у клиента)
        self.ask_human = bool(adef.get("ask_human"))
        self.secret = bool(adef.get("secret"))
        self.timeout = adef.get("timeout")
        self.open_world = bool(adef.get("open_world"))
        self.description = adef.get("description") or {}
        # Сочетания, которые kaskadctl не примет: ровно один из (one_of), не больше одного из (at_most_one) —
        # проверяются здесь, до запуска, а не схемой (anyOf/oneOf верхнего уровня принимают не все клиенты).
        self.one_of = [list(g) for g in adef.get("one_of") or []]
        # Тексты ошибок kaskadctl написаны для человека у командной строки («повторите с --force»): агенту — имена
        # параметров инструмента (стенд T-M6, F4). error_words — свои замены; затем ключи boolean-параметров.
        self.error_words = [(str(a), str(b)) for a, b in (adef.get("error_words") or {}).items()]
        self.at_most_one = [list(g) for g in adef.get("at_most_one") or []]
        self.params = []
        for pname, pdef in (adef.get("params") or {}).items():
            ref = pdef.get("ref", pname)
            ptype = param_types.get(ref)
            if ptype is None:
                log.error("actions.json: инструмент %s ссылается на неизвестный тип %s" % (name, ref))
                ptype = {"type": "string"}
            self.params.append(ParamDef(pname, ptype, pdef, log))
        # Сводный инструмент: действие — параметр action (by_action), см. шапку модуля.
        self.by_action = {}
        self.action_default = adef.get("action_default")
        for aname, vdef in (adef.get("by_action") or {}).items():
            if not re.match(r"^[a-z][a-z0-9_]{0,31}$", str(aname)) or not isinstance(vdef, dict):
                log.error("actions.json: %s — действие %s пропущено" % (name, aname))
                continue
            self.by_action[aname] = Variant.of(self, aname, vdef, log)
        if self.by_action:
            if self.action_default is not None and self.action_default not in self.by_action:
                log.error("actions.json: %s — action_default %s нет в by_action" % (name, self.action_default))
                self.action_default = None
            if self.destructive or self.cmd or any(p.required for p in self.params) or adef.get("confirm"):
                log.error("actions.json: %s — у сводного инструмента cmd, destructive, confirm и обязательность — "
                          "в by_action" % name)
            used = set(p.name for v in self.by_action.values() for p in v.params)
            for p in self.params:
                if p.name not in used:
                    log.error("actions.json: %s — параметр %s не нужен ни одному действию" % (name, p.name))
            head = self.by_action.get(self.action_default) or next(iter(self.by_action.values()))
            self.cmd, self.static_args = list(head.cmd), list(head.static_args)
            self.readonly = all(v.readonly for v in self.by_action.values())
            self.destructive = False                   # confirm — у действия (Variant.destructive)
        if not self.cmd:
            log.error("actions.json: у инструмента %s нет cmd" % name)
        # Цель подтверждения: значение параметра (по умолчанию первого) или имя инструмента («@tool»).
        confirm = adef.get("confirm")
        if confirm == "@tool" or (confirm is None and not self.params):
            self.confirm_param = None
        else:
            self.confirm_param = confirm or self.params[0].name
            if self.confirm_param not in [p.name for p in self.params]:
                log.error("actions.json: confirm инструмента %s — неизвестный параметр %s" % (name, self.confirm_param))
        self.stdin_param = next((p for p in self.params if p.stdin), None)
        self.plain = Variant(None, self.cmd, self.static_args, self.readonly, self.destructive, self.params,
                             self.confirm_param, self.one_of, self.at_most_one)

    def variants(self):
        """Действия инструмента: у сводного — из by_action, у обычного — он сам."""
        return list(self.by_action.values()) if self.by_action else [self.plain]

    def variant(self, action=None):
        """Действие по значению action (None — по умолчанию); нет такого — None."""
        if not self.by_action:
            return self.plain
        return self.by_action.get(self.action_default if action is None else action)

    def agent_words(self, text):
        if not isinstance(text, str):
            return text
        for a, b in self.error_words:
            text = text.replace(a, b)
        for p in self.params:
            if p.flag and p.type == "boolean":
                text = text.replace("с " + p.flag, "с %s=true" % p.name).replace(p.flag, "%s=true" % p.name)
            elif p.flag and p.phrase is not None:
                text = text.replace(p.flag, p.name)
        return text

    @property
    def kind(self):
        if self.destructive or any(v.destructive for v in self.by_action.values()):
            return "destructive"
        return "read" if self.readonly else "write"

    def to_mcp(self, lang="ru"):
        """Описание инструмента на одном языке (KASKAD_MCP_LANG): каталог на двух весил ~70 КБ контекста модели."""
        properties = {}
        required = []
        for spec in self.params:
            properties[spec.name] = spec.json_schema(lang)
            if spec.required:
                required.append(spec.name)
            if spec.danger:
                properties["confirm_" + spec.name] = {"type": "string",
                                                      "description": msg(lang, "danger_desc", name=spec.name)}
        description = pick(self.description, lang)
        if self.destructive:
            if self.confirm_param:
                cdesc = msg(lang, "confirm_desc", name=self.confirm_param)
                note = msg(lang, "destructive_note", name=self.confirm_param)
            else:
                cdesc = msg(lang, "confirm_desc_toolname")
                note = msg(lang, "destructive_note_tool", name=self.name)
            properties["confirm"] = {"type": "string", "description": cdesc}
            required.append("confirm")
            description = _sentence(description, note)
        for group in self.one_of:
            description = _sentence(description, msg(lang, "one_of_note", names=", ".join(group)))
        for group in self.at_most_one:
            description = _sentence(description, msg(lang, "at_most_one_note", names=", ".join(group)))
        if self.by_action:
            properties, description = self._action_schema(properties, required, description, lang)
        schema = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        return {
            "name": self.name,
            "description": description,
            "inputSchema": schema,
            "annotations": self._annotations(),
        }

    def _annotations(self):
        """Только то, что отличается от умолчаний MCP или значимо (3.3.7.4 Ш2а, SPEC-mcp «Размер каталога»).
        idempotentHint не отдаём: он значим только при readOnlyHint=false и по умолчанию false — у нас было ровно это
        (true лишь у read-only, где его не читают; как wt-vless Ш2-б). readOnlyHint=false — умолчание MCP, у изменяющих
        не пишем. destructiveHint значим только при readOnlyHint=false: у read-only не пишем, у изменяющих — всегда
        (умолчание MCP — true, а false у нас — осознанное «клиент не спрашивает»). openWorldHint — всегда (умолчание
        true, у нас почти везде false)."""
        if self.readonly:
            return {"readOnlyHint": True, "openWorldHint": self.open_world}
        return {"destructiveHint": bool(self.destructive or self.ask_human or self.kind == "destructive"),
                "openWorldHint": self.open_world}

    def _action_schema(self, properties, required, description, lang):
        """Сводный инструмент: action первым (enum действий), confirm — если есть разрушительное действие; в описании —
        параметры каждого действия (* — обязательный) одной строкой: схема их не выражает без oneOf, который принимают не
        все клиенты (проверяет сервер, validate_call)."""
        act = {"type": "string", "enum": list(self.by_action)}
        if self.action_default is not None:
            act["default"] = self.action_default
        else:
            required.insert(0, "action")
        out = {"action": act}
        out.update(properties)
        groups = []        # действия с одинаковыми параметрами — вместе: «on/off (…)»; шире прежнего — «(= preview + code*)»
        for v in self.by_action.values():
            mine = [p.name + ("*" if p.required else "") for p in v.params]
            for g in groups:
                if g[1] == mine:
                    g[0].append(v.name)
                    break
            else:
                groups.append([[v.name], mine])
        said = []
        for i, (names, mine) in enumerate(groups):
            base = next((g for g in groups[:i] if g[1] and len(g[1]) < len(mine) and mine[:len(g[1])] == g[1]), None)
            if base is not None:
                ps = "= %s + %s" % (base[0][0], ", ".join(mine[len(base[1]):]))
            else:
                ps = ", ".join(mine)
            said.append("/".join(names) + (" (%s)" % ps if ps else ""))
        line = "; ".join(said)
        description = _sentence(description, msg(lang, "actions_note", list=line))
        for key, names in self._destructive_actions().items():
            if key:
                out["confirm"] = {"type": "string", "description": msg(lang, "confirm_desc", name=key)}
                note = msg(lang, "destructive_note_action", actions="/".join(names), name=key)
            else:
                out["confirm"] = {"type": "string", "description": msg(lang, "confirm_desc_toolname")}
                note = msg(lang, "destructive_note_action_tool", actions="/".join(names), name=self.name)
            description = _sentence(description, note)
        return out, description

    def _destructive_actions(self):
        """{цель confirm (имя параметра или None — имя инструмента): [действия]} разрушительных действий."""
        out = {}
        for v in self.by_action.values():
            if v.destructive:
                out.setdefault(v.confirm_param, []).append(v.name)
        return out


class Catalog(object):
    def __init__(self, spec, lang, log, base_dir):
        self.lang = lang
        self.log = log
        self.base_dir = base_dir
        self.param_types = spec.get("param_types") or {}
        self.tools = {}
        for name, adef in (spec.get("actions") or {}).items():
            if not isinstance(adef, dict):
                log.error("actions.json: инструмент %s описан не объектом — пропущен" % name)
                continue
            if not re.match(r"^[a-z][a-z0-9_]{0,63}$", name):
                log.error("actions.json: имя инструмента %s не годится для MCP — пропущено" % name)
                continue
            self.tools[name] = ToolDef(name, adef, self.param_types, log)
        hidden = spec.get("hidden") or {}
        self.hidden = dict(hidden) if isinstance(hidden, dict) else {}
        self.resources = []
        for rdef in spec.get("resources") or []:
            res = self._resource(rdef)
            if res is not None:
                self.resources.append(res)
        self.resources_by_uri = dict((r["uri"], r) for r in self.resources)
        self.prompts = [p for p in spec.get("prompts") or [] if isinstance(p, dict) and p.get("name")]
        self.prompts_by_name = dict((p["name"], p) for p in self.prompts)

    def _resource(self, rdef):
        if not isinstance(rdef, dict) or not rdef.get("uri"):
            self.log.error("actions.json: ресурс без uri пропущен")
            return None
        uri = str(rdef["uri"])
        if rdef.get("file"):
            path = str(rdef["file"])
            if not os.path.isabs(path):
                path = os.path.normpath(os.path.join(self.base_dir, path))
            if not os.path.isfile(path):
                self.log.warn("ресурс %s: файл %s не найден — чтение будет отвечать -32002" % (uri, path))
            return {"uri": uri, "kind": "file", "path": path, "name": str(rdef.get("name") or os.path.basename(path)),
                    "mime": str(rdef.get("mimeType") or "text/plain"), "description": pick(rdef.get("description"), self.lang)}
        tool = self.tools.get(rdef.get("tool"))
        if tool is None:
            self.log.error("actions.json: ресурс %s ссылается на неизвестный инструмент %s — пропущен"
                           % (uri, rdef.get("tool")))
            return None
        head = tool.variant(None)
        if head is None or not head.readonly or tool.secret or any(p.required for p in head.params):
            # Ресурсы клиенты читают сами, без человека: только чтение без секретов и без обязательных параметров.
            self.log.error("actions.json: ресурс %s указывает на инструмент %s, который не годится для чтения "
                           "без человека — пропущен" % (uri, tool.name))
            return None
        return {"uri": uri, "kind": "tool", "tool": tool, "name": str(rdef.get("name") or tool.name),
                "mime": "application/json", "description": pick(rdef.get("description") or tool.description, self.lang)}

    # --- списки для протокола ---------------------------------------------------------------

    def tools_list(self):
        return [tool.to_mcp(self.lang) for tool in self.tools.values()]

    def resources_list(self):
        out = []
        for res in self.resources:
            item = {"uri": res["uri"], "name": res["name"], "mimeType": res["mime"]}
            if res.get("description"):
                item["description"] = res["description"]
            out.append(item)
        return out

    def resources_by_kind(self, kind):
        return [r for r in self.resources if r["kind"] == kind]

    def prompt_args(self, pdef):
        out = []
        for adef in pdef.get("arguments") or []:
            arg = {"name": str(adef.get("name")), "required": bool(adef.get("required"))}
            ptype = self.param_types.get(adef.get("ref") or arg["name"])
            if ptype and ptype.get("description"):
                arg["description"] = pick(ptype["description"], self.lang)
            out.append(arg)
        return out

    def prompts_list(self):
        return [{"name": p["name"], "description": pick(p.get("description"), self.lang), "arguments": self.prompt_args(p)}
                for p in self.prompts]

    def names_by_kind(self):
        tools = list(self.tools.values())
        return ([t.name for t in tools if t.kind == "read"], [t.name for t in tools if t.kind == "write"],
                [t.name for t in tools if t.kind == "destructive" or t.ask_human])

    # --- валидация и командная строка ------------------------------------------------------

    def validate_value(self, spec, value, lang):
        name = spec.name
        if spec.type == "integer":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    (isinstance(value, float) and not value.is_integer()):
                raise RpcError(INVALID_PARAMS, msg(lang, "err_type", name=name, type="integer"), {"param": name})
            value = int(value)
        elif spec.type == "boolean":
            if not isinstance(value, bool):
                raise RpcError(INVALID_PARAMS, msg(lang, "err_type", name=name, type="boolean"), {"param": name})
        else:
            if not isinstance(value, str):
                raise RpcError(INVALID_PARAMS, msg(lang, "err_type", name=name, type="string"), {"param": name})
            # Текст для stdin (ключ, .conf) многострочный по природе: перевод строки и табуляция там законны, прочие
            # управляющие символы — нет. В argv их не пускаем никогда: перевод строки обходит «$» регексов.
            checked = value.replace("\n", "").replace("\r", "").replace("\t", "") if spec.stdin else value
            if CONTROL_CHARS_RE.search(checked):
                raise RpcError(INVALID_PARAMS, msg(lang, "err_control", name=name), {"param": name})
            if spec.max_length is not None and len(value) > spec.max_length:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_max", name=name, limit=spec.max_length), {"param": name})
            if spec.min_length is not None and len(value) < spec.min_length:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_min", name=name, limit=spec.min_length), {"param": name})
            if spec.pos and value.startswith("-") and value != "-":
                # Позиционный аргумент с «-» argparse принял бы за ключ (--force и т. п.): не пускаем никогда.
                raise RpcError(INVALID_PARAMS, msg(lang, "err_dash", name=name), {"param": name})
            if spec.regex_broken:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_pattern_broken", name=name), {"param": name})
            if spec.regex is not None and spec.regex.search(value) is None:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_pattern", name=name, pattern=spec.pattern),
                               {"param": name, "pattern": spec.pattern})
        if spec.enum is not None and value not in spec.enum:
            raise RpcError(INVALID_PARAMS, msg(lang, "err_enum", name=name, values=", ".join(map(str, spec.enum))),
                           {"param": name, "enum": list(spec.enum)})
        if spec.minimum is not None and isinstance(value, (int, float)) and value < spec.minimum:
            raise RpcError(INVALID_PARAMS, msg(lang, "err_min", name=name, limit=spec.minimum), {"param": name})
        if spec.maximum is not None and isinstance(value, (int, float)) and value > spec.maximum:
            raise RpcError(INVALID_PARAMS, msg(lang, "err_max", name=name, limit=spec.maximum), {"param": name})
        return value

    def pick_variant(self, tool, arguments, lang):
        """Действие вызова: у обычного инструмента — он сам; у сводного — по action (нет — по умолчанию)."""
        if not tool.by_action:
            return tool.plain
        action = arguments.get("action")
        if action is None:
            var = tool.variant(None)
            if var is None:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_missing", names="action"), {"missing": ["action"]})
            return var
        if not isinstance(action, str) or action not in tool.by_action:
            text, data = msg(lang, "err_enum", name="action", values=", ".join(tool.by_action)), \
                {"param": "action", "enum": list(tool.by_action)}
            other = "%s_%s" % (tool.name, action) if isinstance(action, str) else None
            if other in self.tools:                    # host_tariff(action=set) -> host_tariff_set (REV-ns-b14-3 П-2)
                text += msg(lang, "err_action_tool", tool=other)
                data["tool"] = other
            raise RpcError(INVALID_PARAMS, text, data)
        return tool.by_action[action]

    def validate_call(self, tool, arguments, lang):
        """Полная проверка tools/call → (чистые аргументы, хвост argv kaskadctl, текст для stdin или None). У сводного
        инструмента — по его действию (``pick_variant``): свои параметры, обязательные, confirm, команда."""
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, msg(lang, "err_args_object"))
        var = self.pick_variant(tool, arguments, lang)
        allowed = (["action"] if tool.by_action else []) + [p.name for p in var.params] + \
            (["confirm"] if var.destructive else []) + ["confirm_" + p.name for p in var.params if p.danger]
        unknown = [k for k in arguments if k not in allowed]
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            mine = set(p.name for p in tool.params) | set("confirm_" + p.name for p in tool.params if p.danger)
            if tool.by_action and (set(unknown) <= mine | {"confirm"}):
                raise RpcError(INVALID_PARAMS, msg(lang, "err_action_params", names=names, action=var.name,
                                                   allowed=", ".join(allowed)),
                               {"unknown": sorted(map(str, unknown)), "action": var.name})
            raise RpcError(INVALID_PARAMS, msg(lang, "err_unknown_params", names=names,
                                               allowed=", ".join(allowed) or "—"), {"unknown": sorted(map(str, unknown))})
        clean = {}
        if tool.by_action:
            clean["action"] = var.name
        missing = []
        for spec in var.params:
            value = arguments.get(spec.name)
            if value is None:           # null — «не передано»: модели часто шлют null для необязательных
                if spec.required:
                    missing.append(spec.name)
                continue
            clean[spec.name] = self.validate_value(spec, value, lang)
        if var.destructive and arguments.get("confirm") is None:
            missing.append("confirm")
        if missing:
            raise RpcError(INVALID_PARAMS, msg(lang, "err_missing", names=", ".join(missing)), {"missing": missing})
        given = [n for n, v in clean.items() if v is not False]
        for group in var.one_of:
            if len([n for n in group if n in given]) != 1:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_one_of", names=", ".join(group)), {"one_of": group})
        for group in var.at_most_one:
            if len([n for n in group if n in given]) > 1:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_at_most_one", names=", ".join(group)), {"at_most_one": group})
        for spec in var.params:
            if spec.danger and clean.get(spec.name) is True:
                extra = arguments.get("confirm_" + spec.name)
                if not isinstance(extra, str) or extra != spec.name:
                    raise RpcError(INVALID_PARAMS, msg(lang, "err_danger", name=spec.name),
                                   {"param": "confirm_" + spec.name})
                clean["confirm_" + spec.name] = extra
        for spec in var.params:
            if spec.phrase is not None and spec.name in clean and not spec.phrase_ok(clean[spec.name]):
                ru, en = phrase_texts(spec.phrase)
                data = {"param": spec.name, "phrase": spec.phrase}
                if spec.phrase_block == "vless":
                    block = vless_block(lang, clean.get("site") if isinstance(clean.get("site"), str) else None)
                    if block:
                        data.update(block)
                        text = msg(lang, "err_phrase_vless_block", name=spec.name, ru=ru, en=en, block=block["block"])
                    else:
                        text = msg(lang, "err_phrase_vless", name=spec.name, ru=ru, en=en)
                    raise RpcError(INVALID_PARAMS, text, data)
                block = rf_block(lang)
                if block:
                    data.update(block)
                    text = msg(lang, "err_phrase_block", name=spec.name, ru=ru, en=en, block=block["block"])
                else:
                    text = msg(lang, "err_phrase", name=spec.name, ru=ru, en=en)
                raise RpcError(INVALID_PARAMS, text, data)
        if var.destructive:
            confirm = arguments.get("confirm")
            if var.confirm_param:
                target = clean.get(var.confirm_param)
                if target is None or not isinstance(confirm, str) or confirm != self.cli_value(target):
                    raise RpcError(INVALID_PARAMS, msg(lang, "err_confirm", name=var.confirm_param),
                                   {"param": "confirm", "expected_param": var.confirm_param})
            elif not isinstance(confirm, str) or confirm != tool.name:
                raise RpcError(INVALID_PARAMS, msg(lang, "err_confirm_toolname", name=tool.name), {"param": "confirm"})
            clean["confirm"] = confirm
        positional, flags, stdin_text = [], [], None
        for spec in var.params:
            if spec.name not in clean:
                continue
            value = clean[spec.name]
            if spec.stdin:
                stdin_text = value
            elif spec.pos:
                if spec.split:
                    positional.extend(x for x in str(value).split(spec.split) if x)
                else:
                    positional.append(self.cli_value(value))
            elif spec.type == "boolean" or spec.phrase is not None:
                if value:                         # фраза проверена выше: в argv — только флаг, без текста
                    flags.append(spec.flag)
            else:
                # «--k=значение», а не два слова: значение с «-» не станет ключом.
                flags.append("%s=%s" % (spec.flag, self.cli_value(value)))
        return clean, list(var.cmd) + positional + flags + list(var.static_args) + ["--json"], stdin_text

    @staticmethod
    def cli_value(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    # --- подсказки и инструкции ------------------------------------------------------------

    def render_prompt(self, pdef, arguments, lang):
        name = pdef["name"]
        unset = msg(lang, "prompt_unset")
        lines = []
        intro = pdef.get("intro")
        lines.append(fill_placeholders(pick(intro, lang), arguments, unset) if intro else pick(pdef.get("description"), lang))
        declared = [str(a.get("name")) for a in pdef.get("arguments") or []]
        if declared:
            shown = ["%s=%s" % (a, arguments.get(a) if arguments.get(a) not in (None, "") else unset) for a in declared]
            lines.append("%s: %s" % (msg(lang, "prompt_args"), "; ".join(shown)))
        lines.append("")
        lines.append(msg(lang, "prompt_steps") + ":")
        n = 0
        for step in pdef.get("steps") or []:
            tool_name = step.get("tool")
            if tool_name not in self.tools:
                self.log.warn("подсказка %s: инструмента %s нет в actions.json, шаг пропущен" % (name, tool_name))
                continue
            n += 1
            note = {"ru": step.get("ru"), "en": step.get("en")}
            lines.append("%d. %s — %s" % (n, tool_name, fill_placeholders(pick(note, lang), arguments, unset)))
        if pdef.get("outro"):
            lines.append("")
            lines.append(fill_placeholders(pick(pdef["outro"], lang), arguments, unset))
        lines.append("")
        lines.append(msg(lang, "prompt_footer", destructive=", ".join(self.names_by_kind()[2]) or "—"))
        return "\n".join(lines)

    def instructions(self, server_name, version):
        """initialize.instructions: что за сервер, где гайд, с чего начать, confirm, подсказки, секреты — на языке
        каталога (KASKAD_MCP_LANG). Все списки — из каталога."""
        ro, rw, destructive = self.names_by_kind()
        start = [r["tool"].name for r in self.resources_by_kind("tool")]
        uris = [r["uri"] for r in self.resources_by_kind("tool")]
        guides = ["%s (%s)" % (r["uri"], r["mime"]) for r in self.resources_by_kind("file")]
        prompts = [p["name"] for p in self.prompts]
        secret = [t.name for t in self.tools.values() if t.secret]
        stdin = [t.name for t in self.tools.values() if t.stdin_param is not None]
        j = lambda items: ", ".join(items) or "—"   # noqa: E731
        ru = ["MCP-сервер {name} {ver}: узел Kaskad PRO через kaskadctl --json — тот же путь, что у SSH-меню, бота и "
              "веб-панели, с теми же проверками и замком. Ответ инструмента — JSON kaskadctl; при ok=false или коде "
              "возврата ≠ 0 результат помечен isError: error — причина словами, code — 1 ошибка, 2 неверные параметры, "
              "3 конфликт (занято, уже есть), 4 откат, 5 недоступно здесь, 6 занято другим изменением (повторить позже); "
              "надёжнее смотреть code_name. timeout с running=true — команда ещё идёт: не повторяй, проверь чтением."]
        en = ["MCP server {name} {ver}: a Kaskad PRO node through kaskadctl --json — the same path as the SSH menu, the "
              "bot and the web panel, with the same checks and lock. Every tool returns the kaskadctl JSON; on ok=false "
              "or a non-zero exit the result is flagged isError: error — the reason in words, code — 1 error, 2 bad "
              "parameters, 3 conflict (taken, exists), 4 rolled back, 5 not available here, 6 busy (retry later); rely on "
              "code_name. timeout with running=true — the command still runs: do not repeat it, check by reading."]
        if guides:
            ru.append("Полный гайд для агента — ресурс {guides}: прочитай его до первого изменяющего вызова.")
            en.append("The full agent guide is the resource {guides}: read it before the first mutating call.")
        if start:
            ru.append("С чего начать: {start} (те же данные — ресурсы {uris}). Не меняй то, чего не видел.")
            en.append("Where to start: {start} (the same data as resources {uris}). Do not change what you have not seen.")
        ru.append("Только чтение: {ro}. Изменяющие: {rw}. Разрушительные и необратимые: {destr} — каждому нужен параметр "
                  "confirm, равный значению параметра цели (см. описание) или имени инструмента; подставляй его только "
                  "после явного согласия человека на это конкретное действие. Флаги с потерей данных ({danger}) — ещё "
                  "и свой confirm_<флаг> = имя флага.")
        en.append("Read-only: {ro}. Mutating: {rw}. Destructive and irreversible: {destr} — each requires confirm equal to "
                  "the value of its target parameter (see the description) or to the tool name; pass it only after the "
                  "human's explicit consent to that specific action. Flags that lose data ({danger}) also need their "
                  "own confirm_<flag> = the flag name.")
        if prompts:
            ru.append("Подсказки-сценарии (prompts/get): {prompts} — готовый порядок шагов; когда задача совпадает, следуй им.")
            en.append("Prompt scenarios (prompts/get): {prompts} — a ready step order; follow them when the task matches.")
        ru.append("Секреты (ключи, пароли, ссылки на страницы пользователей) возвращают только {secret} — не пересылай их дальше "
                  "без просьбы и не храни. Ключ выхода ({stdin}) уходит на сервер через stdin и нигде не повторяется. "
                  "Все вызовы пишутся в аудит сервера.")
        en.append("Secrets (keys, passwords, links to users' pages) come only from {secret} — do not forward them without "
                  "a request and do not keep them. An exit key ({stdin}) goes to the server via stdin and is never "
                  "echoed. Every call is written to the server audit log.")
        danger = ["%s.%s" % (t.name, p.name) for t in self.tools.values() for p in t.params if p.danger]
        values = dict(name=server_name, ver=version, ro=j(ro), rw=j(rw), destr=j(destructive), start=j(start),
                      uris=j(uris), guides=j(guides), prompts=j(prompts), secret=j(secret), stdin=j(stdin),
                      danger=j(danger))
        lines = en if self.lang == "en" else ru
        return "\n".join(l.format(**values) for l in lines)


def load_catalog(path, lang, log, base_dir=None):
    with open(path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    if not isinstance(spec, dict) or not isinstance(spec.get("actions"), dict):
        raise ValueError("actions.json: ожидается объект с полем actions")
    return Catalog(spec, lang, log, base_dir or os.path.dirname(os.path.realpath(path)))


# ---------------------------------------------------------------------------------------------
# Сверка каталога с разборщиком kaskadctl (тест полноты и --check)
# ---------------------------------------------------------------------------------------------

def leaf_parsers(parser, path=()):
    """{"gw user add": argparse-разборщик} — все конечные команды kaskadctl."""
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    out = {}
    leaf = True
    for a in subs:
        for name, sp in a.choices.items():
            out.update(leaf_parsers(sp, path + (name,)))
            leaf = False
    if leaf and path:
        out[" ".join(path)] = parser
    elif path and any(a for a in parser._actions if getattr(a, "dest", None) == "json"):
        out[" ".join(path)] = parser              # «gw mtu» — и группа, и команда сама по себе
    return out


def parser_problems(catalog, parser=None):
    """Расхождения каталога с kaskadctl: команда или ключ, которых нет; позиционные не по порядку; enum шире choices;
    обязательное у kaskadctl, необязательное у нас; команда kaskadctl ни в инструментах, ни в hidden."""
    if parser is None:
        from ..cli import build_parser
        parser = build_parser()
    leaves = leaf_parsers(parser)
    probs = []
    used = set()
    for tool in catalog.tools.values():
        for var in tool.variants():
            _variant_problems(tool, var, leaves, used, probs)
    for key in sorted(leaves):
        if key not in used and key not in catalog.hidden:
            probs.append("команда kaskadctl «%s» ни в инструментах, ни в hidden" % key)
    for key in sorted(catalog.hidden):
        if key not in leaves:
            probs.append("hidden: нет команды kaskadctl «%s»" % key)
        elif key in used:
            probs.append("hidden: «%s» скрыта, но её зовёт инструмент" % key)
    return probs


def _variant_problems(tool, var, leaves, used, probs):
    """Расхождения одного действия инструмента с его командой kaskadctl (parser_problems)."""
    who = tool.name + ("" if var.name is None else " action=" + var.name)
    key = " ".join(var.cmd)
    leaf = leaves.get(key)
    if leaf is None:
        probs.append("%s: нет команды kaskadctl «%s»" % (who, key))
        return
    used.add(key)
    opts = {}
    for a in leaf._actions:
        for s in a.option_strings:
            opts[s] = a
    if "--json" not in opts:
        probs.append("%s: у «%s» нет --json" % (who, key))
    posacts = [a for a in leaf._actions if not a.option_strings and not isinstance(a, argparse._SubParsersAction)]
    mine = [p for p in var.params if p.pos]
    if len(mine) > len(posacts):
        probs.append("%s: позиционных больше, чем у «%s»" % (who, key))
    for p, a in zip(mine, posacts):
        if a.nargs in (None, "+") and not p.required:
            probs.append("%s: %s обязателен у kaskadctl" % (who, p.name))
        if p.split and a.nargs not in ("*", "+"):
            probs.append("%s: %s со split, а у kaskadctl одно значение" % (who, p.name))
        if a.choices and p.enum and not set(p.enum) <= set(a.choices):
            probs.append("%s: %s — значения %s шире choices kaskadctl" % (who, p.name, sorted(set(p.enum) - set(a.choices))))
    for a in posacts[len(mine):]:
        if a.nargs in (None, "+"):
            probs.append("%s: обязательный позиционный «%s» не передаётся" % (who, a.dest))
    for p in var.params:
        if not p.flag:
            continue
        a = opts.get(p.flag)
        if a is None:
            probs.append("%s: у «%s» нет ключа %s" % (who, key, p.flag))
            continue
        boolean = isinstance(a, argparse._StoreTrueAction)
        if boolean != (p.type == "boolean" or p.phrase is not None):
            probs.append("%s: %s — у kaskadctl %s" % (who, p.flag, "флаг" if boolean else "значение"))
        if a.choices and p.enum and not set(p.enum) <= set(a.choices):
            probs.append("%s: %s — значения шире choices kaskadctl" % (who, p.flag))
        if a.required and not p.required:
            probs.append("%s: %s обязателен у kaskadctl" % (who, p.flag))
    given = set(p.flag for p in var.params if p.flag)
    for arg in var.static_args:
        flag = arg.split("=", 1)[0]
        if flag not in opts:
            probs.append("%s: у «%s» нет ключа %s" % (who, key, flag))
        given.add(flag)
    for a in leaf._actions:
        if a.option_strings and a.required and not set(a.option_strings) & given:
            probs.append("%s: обязательный ключ %s не передаётся" % (who, a.option_strings[0]))


# ---------------------------------------------------------------------------------------------
# Мост: kaskadctl … --json
# ---------------------------------------------------------------------------------------------

class Bridge(object):
    """kaskadctl отдельным процессом. stdout и stderr команды — во временные безымянные файлы, а не в каналы: команда,
    которую перестали ждать, дописывает их спокойно и не падает от SIGPIPE, когда сервер уже вышел.

    Таймаут: команда чтения (и любая без изменений) останавливается — SIGTERM группе, через 3 с SIGKILL; изменяющая —
    НЕ останавливается: внутри может идти apt/dpkg или сборка модуля, и убить её посреди — хуже, чем подождать. Ответ —
    code timeout, running true: «ещё идёт, проверьте чтением». Пока команда идёт, раз в PROGRESS_EVERY секунд зовётся
    progress(прошло_секунд) — сервер шлёт клиенту notifications/progress."""

    PROGRESS_EVERY = 15.0

    def __init__(self, ctl, timeout, lang, log):
        self.ctl = list(ctl)
        self.timeout = timeout
        self.lang = lang
        self.log = log

    def run(self, tail, stdin_text=None, timeout=None, kill_on_timeout=True, progress=None):
        """kaskadctl с хвостом argv → dict. Ошибка запуска, таймаут, ответ без JSON — тоже dict (ok false)."""
        import tempfile
        import threading
        timeout = float(timeout or self.timeout)
        cmd = self.ctl + list(tail)
        # В журнал — только команда и число аргументов: значения (имена людей, ключи) туда не идут.
        self.log.debug("kaskadctl: %s (+%d арг.)" % (" ".join(tail[:3]), max(0, len(tail) - 3)))
        out_f = tempfile.TemporaryFile()
        err_f = tempfile.TemporaryFile()
        try:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL if stdin_text is None else subprocess.PIPE,
                                        stdout=out_f, stderr=err_f, start_new_session=True)
            except (OSError, ValueError) as exc:
                return {"ok": False, "code": "transport", "code_name": "transport",
                        "error": msg(self.lang, "transport_cannot_run", bin=self.ctl[0], error=str(exc))}
            if stdin_text is not None:
                payload = stdin_text.encode("utf-8")

                def feed():
                    try:
                        proc.stdin.write(payload)
                    except OSError:
                        pass
                    finally:
                        try:
                            proc.stdin.close()
                        except OSError:
                            pass
                threading.Thread(target=feed, daemon=True).start()
            started = time.time()
            timed_out = False
            while True:
                left = timeout - (time.time() - started)
                if left <= 0:
                    timed_out = True
                    break
                try:
                    proc.wait(timeout=min(left, self.PROGRESS_EVERY))
                    break
                except subprocess.TimeoutExpired:
                    if progress is not None and time.time() - started < timeout:
                        try:
                            progress(int(time.time() - started))
                        except Exception:  # noqa: BLE001 — уведомление не должно ронять вызов
                            pass
            if timed_out:
                if kill_on_timeout:
                    self._kill_group(proc)
                self.log.warn("kaskadctl %s: таймаут %.0f с%s" % (" ".join(tail[:3]), timeout,
                                                                  "" if kill_on_timeout else " — не остановлена, идёт"))
                err_text = self._read(err_f)
                doc = {"ok": False, "code": "timeout", "code_name": "timeout", "timeout_s": timeout,
                       "running": not kill_on_timeout,
                       "error": msg(self.lang, "timeout" if kill_on_timeout else "timeout_running", seconds=int(timeout)),
                       "stderr_tail": util.redact(self._tail(err_text, 10))}
                if not kill_on_timeout:
                    doc["pid"] = proc.pid
                return doc
            out_text, err_text = self._read(out_f), self._read(err_f)
        finally:
            out_f.close()
            err_f.close()
        err_text = ANSI_RE.sub("", err_text)
        if err_text.strip():
            self.log.debug("kaskadctl stderr: %s" % util.redact(self._tail(err_text, 20)))
        rc = proc.returncode
        doc = self.parse(out_text)
        if doc is None:
            if rc == 2:
                # Разбор аргументов kaskadctl (argparse) отказал: это неверные параметры, а не сбой связи.
                return {"ok": False, "code": "usage", "code_name": "usage", "exit_code": rc,
                        "error": msg(self.lang, "usage_no_json"),
                        "stderr_tail": util.redact(self._tail(err_text, 5))}
            self.log.warn("kaskadctl %s: нет JSON (код %s)" % (" ".join(tail[:3]), rc))
            return {"ok": False, "code": "transport", "code_name": "transport", "exit_code": rc,
                    "error": msg(self.lang, "transport_no_json", rc=rc),
                    "stderr_tail": util.redact(self._tail(err_text, 10))}
        if rc:
            doc["exit_code"] = rc
            if doc.get("ok") is None:
                doc["ok"] = False
            if isinstance(doc.get("code"), int) and not isinstance(doc.get("code"), bool):
                doc["code_name"] = CODE_NAMES.get(doc["code"], "error")
            elif "code_name" not in doc:
                doc["code_name"] = CODE_NAMES.get(rc, "error")
            warn = [l for l in err_text.splitlines() if l.strip()]
            if warn and "error" not in doc:
                doc["stderr_tail"] = util.redact("\n".join(warn[-5:]))
        return doc

    @staticmethod
    def _read(f, limit=8 * 1024 * 1024):
        try:
            f.seek(0)
            return f.read(limit).decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""

    @staticmethod
    def parse(text):
        """JSON-документ stdout: kaskadctl --json печатает ровно один; на случай строк до него — последняя строка-объект."""
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped, parse_constant=_no_constant)
                if isinstance(obj, dict):
                    return obj
            except (ValueError, RecursionError):
                pass
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line, parse_constant=_no_constant)
                except (ValueError, RecursionError):
                    continue
                if isinstance(obj, dict):
                    return obj
        return None

    @staticmethod
    def _tail(text, n_lines, limit=2000):
        lines = [l for l in text.splitlines() if l.strip()]
        return "\n".join(lines[-n_lines:])[-limit:]

    def _kill_group(self, proc):
        for sig, wait in ((signal.SIGTERM, KILL_GRACE), (signal.SIGKILL, KILL_GRACE)):
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                return
            try:
                proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue


# ---------------------------------------------------------------------------------------------
# Аудит
# ---------------------------------------------------------------------------------------------

class Audit(object):
    """Строка JSON на вызов: время, инструмент, команда, параметры без секретов, статус, клиент, адрес SSH. Ответ
    kaskadctl в аудит не попадает никогда (там бывают ключи). Файл 0600."""

    def __init__(self, path, log):
        self.path = path
        self.log = log
        self.failed = False
        ssh = os.environ.get("SSH_CONNECTION", "").split()
        self.peer = ssh[0] if ssh else "local"
        # порт клиента: по паре адрес+порт панель находит в журнале sshd отпечаток ключа агента (kaskad.mcp.seen)
        self.port = ssh[1] if len(ssh) > 1 and ssh[1].isdigit() else ""

    def writable(self):
        """Можно ли дописать аудит (файл или его каталог доступны на запись)."""
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d, 0o755)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.close(fd)
            return True
        except OSError:
            return False

    @staticmethod
    def scrub(args):
        return dict((k, "***" if SENSITIVE_PARAM_RE.search(k) else v) for k, v in (args or {}).items())

    def write(self, kind, tool, command, args, status, code, ms, client="-"):
        params = self.scrub(args)
        if status == "rejected":
            # Отклонённый вызов — только имена и размеры: в отказ приходит что угодно.
            params = dict((clip_text(k, 40), "<%s %d>" % (type(v).__name__, len(json.dumps(v, ensure_ascii=False))))
                          for k, v in list((args or {}).items())[:20])
        rec = {"ts": utc_now(), "iface": "mcp", "kind": kind, "tool": clip_text(tool, 80),
               "command": clip_text(command, 120), "params": params, "status": status,
               "code": code if code is not None else "", "ms": ms, "client": clip_text(client, 100),
               "from": self.peer, "port": self.port}
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        if len(line) > AUDIT_LINE_MAX:
            rec["params"] = {"_truncated": True, "_len": len(line)}
            line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d, 0o755)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as exc:
            if not self.failed:
                self.failed = True
                self.log.warn("аудит %s недоступен: %s" % (self.path, exc))


# ---------------------------------------------------------------------------------------------
# Сервер JSON-RPC / MCP
# ---------------------------------------------------------------------------------------------

def _no_constant(name):
    """NaN, Infinity, -Infinity — не JSON (RFC 8259): отказ разбора, а не float, который потом не сериализуется."""
    raise ValueError("%s is not valid JSON" % name)


class Server(object):
    NOTIFICATIONS = ("notifications/initialized", "notifications/cancelled", "notifications/progress",
                     "notifications/roots/list_changed")

    def __init__(self, settings, catalog, bridge, audit, log):
        self.settings = settings
        self.catalog = catalog
        self.bridge = bridge
        self.audit = audit
        self.log = log
        self.lang = settings.lang
        self.initialized = False
        self.client = "-"
        self.notify = None                 # serve() ставит запись уведомления в stdout (notifications/progress)
        self.methods = {
            "initialize": self.m_initialize,
            "ping": self.m_ping,
            "tools/list": self.m_tools_list,
            "tools/call": self.m_tools_call,
            "resources/list": self.m_resources_list,
            "resources/read": self.m_resources_read,
            "resources/templates/list": self.m_resources_templates_list,
            "prompts/list": self.m_prompts_list,
            "prompts/get": self.m_prompts_get,
        }

    def handle_line(self, raw):
        """Одна строка stdin → список ответов (0, 1 или пакет)."""
        try:
            # NaN/Infinity и число за ±util.NUM_MAX (1e400 — это inf, целое из 400 цифр) — отказ разбора (REV-33714 С-1)
            message = util.json_in(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            # RecursionError — сотни тысяч «[»: глубина разбора, а не сбой сервера
            return [self._error_response(None, PARSE_ERROR, "Parse error: %s" % clip_text(exc, 200))]
        if isinstance(message, list):
            # Пакеты убраны из 2025-06-18, но клиенты 2025-03-26 могут их слать.
            if not message:
                return [self._error_response(None, INVALID_REQUEST, "Invalid Request: empty batch")]
            responses = [r for r in (self.handle_message(item) for item in message) if r is not None]
            return [responses] if responses else []
        response = self.handle_message(message)
        return [response] if response is not None else []

    def handle_message(self, message):
        if not isinstance(message, dict):
            return self._error_response(None, INVALID_REQUEST, "Invalid Request: not an object")
        is_request = "id" in message
        msg_id = message.get("id")
        method = message.get("method")
        if method is None:
            if "result" in message or "error" in message:
                return None
            return self._error_response(msg_id, INVALID_REQUEST, "Invalid Request: missing method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return self._error_response(msg_id, INVALID_REQUEST, "Invalid Request: jsonrpc must be \"2.0\"")
        params = message.get("params")
        if params is None:
            params = {}
        if not is_request:
            if method == "notifications/initialized":
                self.initialized = True
            self.log.debug("notification: " + clip_text(method, 64))
            return None
        if not isinstance(params, dict):
            return self._error_response(msg_id, INVALID_PARAMS, "Invalid params: params must be an object")
        handler = self.methods.get(method)
        if handler is None:
            return self._error_response(msg_id, METHOD_NOT_FOUND, "Method not found: " + clip_text(method, 64))
        try:
            result = handler(params)
        except RpcError as exc:
            return self._error_response(msg_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001 — ни одна ошибка не должна уронить сессию
            self.log.error("%s: %s\n%s" % (method, exc, traceback.format_exc()))
            return self._error_response(msg_id, INTERNAL_ERROR, "Internal error: %s" % exc)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error_response(msg_id, code, message, data=None):
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": msg_id, "error": error}

    def _audit(self, kind, tool, command, args, status, code, ms):
        self.audit.write(kind, tool, command, args, status, code, ms, client=self.client)

    # --- методы MCP ---------------------------------------------------------------------------

    def m_initialize(self, params):
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        self.client = "%s/%s" % (clip_text(client.get("name", "?"), 60), clip_text(client.get("version", "?"), 30))
        self.log.info("initialize: client %s, protocol %s -> %s" % (self.client, clip_text(requested, 20), version))
        # первый вызов агента: панель видит по нему «ключ подключён» (kaskad.mcp.seen)
        self._audit("initialize", "", "initialize", {}, "ok", None, 0)
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False},
                             "prompts": {"listChanged": False}},
            "serverInfo": {"name": self.settings.server_name, "version": self.settings.version},
            "instructions": self.catalog.instructions(self.settings.server_name, self.settings.version),
        }

    def m_ping(self, params):
        return {}

    def m_tools_list(self, params):
        return {"tools": self.catalog.tools_list()}

    @staticmethod
    def _command_of(var):
        """Команда в аудит. Чтение — с постоянными ключами: предпросмотр ``host tariff set --dry`` и цена
        ``host bench --dry`` — чтения, а без ключа выглядят как изменение и запуск проверки (REV-ns-b14-3 П-3)."""
        return " ".join(var.cmd + (var.static_args if var.readonly else []))

    @classmethod
    def _asked_command(cls, tool, arguments):
        """Команда для аудита до проверки вызова: у сводного — действие, которое просили (а не по умолчанию);
        неизвестное действие — имя инструмента (REV-ns-b14-2 П-1)."""
        if not tool.by_action:
            return cls._command_of(tool.plain)
        action = arguments.get("action") if isinstance(arguments, dict) else None
        var = tool.variant(action) if action is None or isinstance(action, str) else None
        return cls._command_of(var) if var is not None else tool.name

    def m_tools_call(self, params):
        name = params.get("name")
        tool = self.catalog.tools.get(name) if isinstance(name, str) else None
        if tool is None:
            raise RpcError(INVALID_PARAMS, msg(self.lang, "err_unknown_tool", name=clip_text(name, 80)))
        arguments = params.get("arguments")
        command = self._asked_command(tool, arguments)
        try:
            clean, tail, stdin_text = self.catalog.validate_call(tool, arguments, self.lang)
            var = tool.variant(clean.get("action")) if tool.by_action else tool.plain
            command = self._command_of(var)
        except RpcError:
            # Отклонённые вызовы — тоже в аудит: разрушительное без confirm — событие безопасности.
            self._audit("call", tool.name, command, arguments if isinstance(arguments, dict) else {},
                        "rejected", "invalid_params", 0)
            raise
        if var.stdin_param is not None and var.stdin_param.name in clean:
            clean = dict(clean)
            clean[var.stdin_param.name] = "<%d chars>" % len(stdin_text or "")       # в аудит — длина, не ключ
        if (not var.readonly or tool.secret) and not self.audit.writable():
            # Изменение, которое не попадёт в аудит, не делаем: след важнее удобства. Чтение — можно.
            raise RpcError(INTERNAL_ERROR, msg(self.lang, "err_audit", path=self.audit.path), {"audit": "unavailable"})
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        token = meta.get("progressToken")
        progress = None
        if isinstance(token, (str, int)) and not isinstance(token, bool) and self.notify is not None:
            limit = float(tool.timeout or self.settings.timeout)

            def progress(elapsed):
                self.notify({"jsonrpc": "2.0", "method": "notifications/progress",
                             "params": {"progressToken": token, "progress": elapsed, "total": limit,
                                        "message": msg(self.lang, "progress", tool=tool.name, seconds=elapsed)}})
        started = time.time()
        response = self.bridge.run(tail, stdin_text=stdin_text, timeout=tool.timeout or self.settings.timeout,
                                   kill_on_timeout=var.readonly, progress=progress)
        stdin_text = None
        ms = int((time.time() - started) * 1000)
        failed = response.get("ok") is False or bool(response.get("exit_code"))
        status = "error" if failed else "success"
        code = response.get("code_name") if failed else None
        self._audit("call", tool.name, command, clean, status, code, ms)
        response = self.for_agent(tool, response)
        if not var.readonly:
            util.log_action("MCP %s: %s %s → %s%s" % (self.client, tool.name, json.dumps(Audit.scrub(clean),
                                                                                       ensure_ascii=False),
                                                      "ошибка" if failed else "готово", (" (%s)" % code) if code else ""))
        self.log.info("tools/call %s -> %s (%d ms)" % (tool.name, status, ms))
        return self.tool_result(response, failed)

    @staticmethod
    def for_agent(tool, response):
        """Ответ для модели: «apply» (подробности применения шлюза — DNS, nft, xray, ~2,5 КБ) убирается, его
        предупреждения поднимаются в warnings (стенд T-M6, F15); слова командной строки в ошибке — именами параметров."""
        out = dict(response)
        apply = out.pop("apply", None)
        if isinstance(apply, dict) and apply.get("warnings"):
            out["warnings"] = list(out.get("warnings") or []) + [w for w in apply["warnings"]
                                                                if w not in (out.get("warnings") or [])]
        if isinstance(out.get("error"), str):
            out["error"] = tool.agent_words(out["error"])
        return out

    def tool_result(self, response, failed=None):
        """content (текст + изображения) + structuredContent + isError. Любое поле png_b64 / *_png_b64 (строка или
        список, на любой глубине: у gw issue --png — в записях items) — картинкой; в текстовой копии — заглушка: модели не нужны килобайты base64."""
        if failed is None:
            failed = response.get("ok") is False or bool(response.get("exit_code"))
        images = []

        def walk(node):
            if isinstance(node, dict):
                out = {}
                for key, value in node.items():
                    if isinstance(key, str) and (key == "png_b64" or key.endswith("_png_b64")):
                        out[key] = pngs(value)
                    else:
                        out[key] = walk(value)
                return out
            if isinstance(node, list):
                return [walk(x) for x in node]
            if isinstance(node, str):
                return no_tags(node)
            return node

        def pngs(value):
            items = value if isinstance(value, list) else [value]
            shown = []
            for item in items:
                image = self._png_content(item) if isinstance(item, str) else None
                if image is None:
                    shown.append(item)
                    continue
                images.append(image)
                shown.append(msg(self.lang, "png_placeholder", size=image["_size"], n=len(images)))
            return shown if isinstance(value, list) else shown[0]

        text_view = walk(response)
        # текст — компактный JSON: тот же structuredContent второй раз, без отступов (стенд T-M6, F15)
        content = [{"type": "text", "text": json.dumps(text_view, ensure_ascii=False, separators=(",", ":"))}]
        for image in images:
            content.append({"type": "image", "data": image["data"], "mimeType": "image/png"})
        # structuredContent — тот же вид, что текст: картинки уже лежат блоками image, base64 дважды не шлём
        return {"content": content, "structuredContent": text_view, "isError": bool(failed)}

    def _png_content(self, b64):
        try:
            raw = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            return None
        if not raw.startswith(PNG_SIGNATURE):
            return None
        return {"data": b64, "_size": len(raw)}

    def m_resources_list(self, params):
        return {"resources": self.catalog.resources_list()}

    def m_resources_templates_list(self, params):
        return {"resourceTemplates": []}

    def m_resources_read(self, params):
        uri = params.get("uri")
        res = self.catalog.resources_by_uri.get(uri) if isinstance(uri, str) else None
        if res is None:
            raise RpcError(RESOURCE_NOT_FOUND, "Resource not found", {"uri": clip_text(uri, 200)})
        if res["kind"] == "file":
            return self.read_file_resource(res)
        tool = res["tool"]
        started = time.time()
        response = self.bridge.run(list(tool.cmd) + list(tool.static_args) + ["--json"],
                                   timeout=tool.timeout or self.settings.timeout)
        ms = int((time.time() - started) * 1000)
        failed = response.get("ok") is False or bool(response.get("exit_code"))
        self._audit("resource", tool.name, " ".join(tool.cmd), {"uri": uri}, "error" if failed else "success",
                    response.get("code_name") if failed else None, ms)
        if failed:
            # Ошибка — ошибкой JSON-RPC, а не «успешным» документом: клиент кладёт ресурс в контекст без разбора.
            raise RpcError(INTERNAL_ERROR, clip_text(response.get("error") or "kaskadctl error", 500),
                           dict((k, response.get(k)) for k in ("code", "code_name", "exit_code", "data") if k in response))
        return {"contents": [{"uri": uri, "mimeType": res["mime"],
                              "text": json.dumps(response, ensure_ascii=False, separators=(",", ":"))}]}

    def read_file_resource(self, res):
        """Гайд рядом с сервером; читается при каждом запросе, без кэша. Нет файла — -32002 с путём."""
        uri, path = res["uri"], res["path"]
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            self.log.warn("ресурс %s: не удалось прочитать %s: %s" % (uri, path, exc))
            self._audit("resource", "-", "file:" + os.path.basename(path), {"uri": uri}, "error", "file_missing", 0)
            raise RpcError(RESOURCE_NOT_FOUND, msg(self.lang, "err_resource_file", uri=uri, path=path),
                           {"uri": uri, "path": path})
        item = {"uri": uri, "mimeType": res["mime"]}
        try:
            item["text"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            item["blob"] = base64.b64encode(raw).decode("ascii")
        self._audit("resource", "-", "file:" + os.path.basename(path), {"uri": uri}, "success", None, 0)
        return {"contents": [item]}

    def m_prompts_list(self, params):
        return {"prompts": self.catalog.prompts_list()}

    def m_prompts_get(self, params):
        name = params.get("name")
        pdef = self.catalog.prompts_by_name.get(name) if isinstance(name, str) else None
        if pdef is None:
            raise RpcError(INVALID_PARAMS, msg(self.lang, "err_unknown_prompt", name=clip_text(name, 80)))
        raw_args = params.get("arguments") or {}
        if not isinstance(raw_args, dict):
            raise RpcError(INVALID_PARAMS, msg(self.lang, "err_args_object"))
        arguments = {}
        for key, value in raw_args.items():
            if value is None:
                continue
            arguments[str(key)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        declared = dict((str(a.get("name")), a) for a in pdef.get("arguments") or [])
        missing = [n for n, a in declared.items() if a.get("required") and not arguments.get(n)]
        if missing:
            raise RpcError(INVALID_PARAMS, msg(self.lang, "err_prompt_args", names=", ".join(missing)), {"missing": missing})
        for key in list(arguments):
            adef = declared.get(key)
            if adef is None:
                arguments.pop(key)               # необъявленные аргументы в текст подсказки не попадают
                continue
            ptype = self.catalog.param_types.get(adef.get("ref") or key)
            if ptype:
                arguments[key] = self.catalog.validate_value(ParamDef(key, ptype, {"pos": True}, self.log),
                                                             arguments[key], self.lang)
        text = self.catalog.render_prompt(pdef, arguments, self.lang)
        return {"description": pick(pdef.get("description"), self.lang),
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


# ---------------------------------------------------------------------------------------------
# Транспорт stdio и точка входа
# ---------------------------------------------------------------------------------------------

def debug_rpc_line(raw):
    """Строка журнала debug о сообщении — БЕЗ значений аргументов и результатов (там ключи)."""
    try:
        m = util.json_in(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return "<%d bytes, not JSON>" % len(raw)
    if isinstance(m, list):
        return "<batch of %d>" % len(m)
    if not isinstance(m, dict):
        return "<%s>" % type(m).__name__
    parts = []
    if "method" in m:
        parts.append("method=%s" % clip_text(m.get("method"), 64))
    if "id" in m:
        parts.append("id=%s" % clip_text(m.get("id"), 32))
    params = m.get("params")
    if isinstance(params, dict):
        if "name" in params:
            parts.append("tool=%s" % clip_text(params.get("name"), 64))
        args = params.get("arguments")
        if isinstance(args, dict):
            parts.append("args={%s}" % ", ".join(
                "%s:<%d chars>" % (clip_text(k, 32), len(v) if isinstance(v, str) else len(json.dumps(v, default=str)))
                for k, v in list(args.items())[:24]))
    if isinstance(m.get("error"), dict):
        parts.append("error=%s" % clip_text(m["error"].get("code"), 16))
    elif "result" in m:
        parts.append("result=<%d bytes>" % len(json.dumps(m["result"], default=str)))
    return " ".join(parts) or "<%d bytes>" % len(raw)


def _encode(message, log):
    """Сообщение → строка протокола. Не сериализуется (NaN из ответа команды, глубина) — ошибка -32603 с тем же id,
    а не падение сервера."""
    try:
        return json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        log.error("ответ не сериализуется: %s" % clip_text(exc, 200))
        msg_id = message.get("id") if isinstance(message, dict) else None
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": INTERNAL_ERROR,
                           "message": "Internal error: the answer is not valid JSON"}}).encode("utf-8")


def serve(server, stdin, stdout, log):
    """Цикл: строка → ответ(ы). Один поток: kaskadctl всё равно сериализует изменения замком state.lock. Любая
    ошибка одной строки — ответ с ошибкой, сервер живёт."""
    closed = []

    def write(message):
        if closed:
            return
        data = _encode(message, log)
        if log.level <= 10:
            log.debug("-> " + debug_rpc_line(data))
        try:
            stdout.write(data + b"\n")
            stdout.flush()
        except OSError:
            closed.append(True)

    server.notify = write
    while True:
        raw = stdin.readline()
        if not raw:
            log.info("stdin закрыт, завершение")
            return 0
        raw = raw.strip()
        if not raw:
            continue
        if log.level <= 10:
            log.debug("<- " + debug_rpc_line(raw))
        try:
            responses = server.handle_line(raw)
        except Exception as exc:  # noqa: BLE001 — ни одна строка не должна уронить сессию
            log.error("строка не обработана: %s\n%s" % (clip_text(exc, 200), traceback.format_exc()))
            responses = [Server._error_response(None, INTERNAL_ERROR, "Internal error")]
        for response in responses:
            write(response)
        if closed:
            log.info("stdout закрыт клиентом, завершение")
            return 0


def build(environ, script_dir, log_stream):
    settings = Settings(environ, script_dir)
    log = Log(settings.log_level, log_stream)
    catalog = load_catalog(settings.actions_json, settings.lang, log, script_dir)
    bridge = Bridge(settings.ctl, settings.timeout, settings.lang, log)
    try:
        bridge.PROGRESS_EVERY = max(0.5, float(environ.get(env_name("PROGRESS")) or Bridge.PROGRESS_EVERY))
    except ValueError:
        pass
    audit = Audit(settings.audit_log, log)
    return settings, log, catalog, bridge, audit


KIND_RU = {"read": "чтение", "write": "изменение", "destructive": "**разрушительное**"}


def tools_markdown(catalog):
    """Таблицы инструментов и ресурсов для README (из actions.json, чтобы не расходиться)."""
    rows = ["| Инструмент | Тип | Параметры | Команда kaskadctl | Описание |", "|---|---|---|---|---|"]
    for tool in catalog.tools.values():
        desc = pick(tool.description, "ru").replace("|", "\\|")
        for var in tool.variants():                    # сводный инструмент — строка на действие
            params = ([] if var.name is None else ["`action`=`%s`" % var.name]) + \
                ["`%s`%s" % (p.name, "*" if p.required else "") for p in var.params]
            if var.destructive:
                params.append("`confirm`*" + ("" if var.confirm_param else " (= `%s`)" % tool.name))
            cmd = " ".join(var.cmd + var.static_args)
            kind = "destructive" if var.destructive else ("read" if var.readonly else "write")
            rows.append("| `%s` | %s%s | %s | `%s` | %s |" % (tool.name, KIND_RU[kind], ", секрет" if tool.secret else "",
                                                             ", ".join(params) or "—", cmd, desc))
            desc = "″"
    rows += ["", "| Ресурс | Источник | MIME | Что внутри |", "|---|---|---|---|"]
    for res in catalog.resources:
        source = ("файл `%s`" % os.path.basename(res["path"])) if res["kind"] == "file" else "инструмент `%s`" % res["tool"].name
        rows.append("| `%s` | %s | `%s` | %s |" % (res["uri"], source, res["mime"], res["name"].replace("|", "\\|")))
    return "\n".join(rows)


def check(settings, log, catalog, out=None):
    """--check: что сервер видит; 0 — порядок, 1 — проблемы."""
    out = out or sys.stderr
    ok = True
    out.write("actions.json : %s\n" % settings.actions_json)
    ctl0 = settings.ctl[0]
    runnable = os.access(ctl0, os.X_OK)
    out.write("kaskadctl    : %s%s\n" % (" ".join(settings.ctl[:1]) if len(settings.ctl) == 1 else ctl0 + " -I …",
                                         "" if runnable else "  (НЕ найден или не исполняемый)"))
    ok = ok and runnable
    out.write("версия       : %s\n" % settings.version)
    out.write("аудит        : %s\n" % settings.audit_log)
    out.write("язык/таймаут : %s / %.0f с\n" % (settings.lang, settings.timeout))
    ro, rw, destructive = catalog.names_by_kind()
    out.write("инструменты  : %d (чтение %d, изменение %d, разрушительных %d); скрыто команд kaskadctl: %d\n"
              % (len(catalog.tools), len(ro), len(rw), len(destructive), len(catalog.hidden)))
    files = catalog.resources_by_kind("file")
    out.write("ресурсы      : %d (инструментов %d, файлов %d), подсказок: %d\n"
              % (len(catalog.resources), len(catalog.resources) - len(files), len(files), len(catalog.prompts)))
    broken = sorted(set(p.name for t in catalog.tools.values() for p in t.params if p.regex_broken))
    if broken:
        ok = False
        out.write("ПРОБЛЕМА: шаблоны не компилируются: %s\n" % ", ".join(broken))
    for res in files:
        if not os.path.isfile(res["path"]):
            ok = False
            out.write("ПРОБЛЕМА: нет файла ресурса %s: %s\n" % (res["uri"], res["path"]))
    try:
        probs = parser_problems(catalog)
    except Exception as exc:  # noqa: BLE001 — сверка не должна ронять --check
        probs = ["сверка с kaskadctl не удалась: %s" % exc]
    for p in probs:
        ok = False
        out.write("ПРОБЛЕМА: %s\n" % p)
    out.write("OK\n" if ok else "ЕСТЬ ПРОБЛЕМЫ\n")
    return 0 if ok else 1


USAGE = ("usage: kaskad-mcp [--check | --tools-md | --version]\n"
         "Без аргументов — MCP-сервер по stdio (JSON-RPC 2.0, MCP %s): ssh root@HOST kaskad-mcp\n"
         "--check     сводка настроек, каталога и сверка с kaskadctl (код 0/1)\n"
         "--tools-md  таблицы инструментов и ресурсов для README\n" % PROTOCOL_VERSION)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    script_dir = os.path.dirname(os.path.realpath(__file__))
    if argv and argv[0] in ("-h", "--help"):
        sys.stderr.write(USAGE)
        return 0
    if argv and argv[0] in ("--version", "-V"):
        sys.stdout.write("%s-mcp %s (MCP %s)\n" % (PRODUCT, __version__, PROTOCOL_VERSION))
        return 0
    if argv and argv[0] not in ("--check", "--tools-md"):
        sys.stderr.write("%s-mcp: неизвестный аргумент %s\n%s" % (PRODUCT, clip_text(argv[0], 64), USAGE))
        return 2
    # stdout священен: забираем бинарный поток протокола себе, sys.stdout — в stderr.
    proto_out = sys.stdout.buffer
    proto_in = sys.stdin.buffer
    sys.stdout = sys.stderr
    try:
        settings, log, catalog, bridge, audit = build(os.environ, script_dir, sys.stderr)
    except (OSError, ValueError) as exc:
        sys.stderr.write("%s-mcp: не удалось загрузить каталог: %s\n" % (PRODUCT, exc))
        return 2
    if argv and argv[0] == "--tools-md":
        proto_out.write((tools_markdown(catalog) + "\n").encode("utf-8"))
        return 0
    if argv and argv[0] == "--check":
        return check(settings, log, catalog)
    log.info("старт: %s %s, MCP %s, %d инструментов, %d ресурсов, %d подсказок"
             % (settings.server_name, settings.version, PROTOCOL_VERSION, len(catalog.tools), len(catalog.resources),
                len(catalog.prompts)))
    server = Server(settings, catalog, bridge, audit, log)
    try:
        return serve(server, proto_in, proto_out, log)
    except KeyboardInterrupt:
        return 0

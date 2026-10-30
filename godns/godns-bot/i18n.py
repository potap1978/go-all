#!/usr/bin/env python3
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
"""goDNS bot — i18n: t()/tf() и язык на пользователя.

Приём целиком из goTelegram (gotelegram-bot/i18n.py): словари в
lang/<code>.json рядом с модулем, выбор пользователя — в JSON-файле
состояния. Отличия: язык по умолчанию берётся из /opt/godns/.language
(его пишет SSH-меню goDNS), падение по цепочке — на русский: оператор
этого продукта русскоязычный, и «не смог определить» должно означать
русский, а не английский (контракт lib/i18n.sh).

t(user_id, key) печатает САМ КЛЮЧ, если строки нет, — пустая строка
невидима и уезжает в релиз, ключ виден сразу (тот же контракт).
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
LANG_DIR = _MODULE_DIR / "lang"

# Состояние — в подкаталоге, куда пишет НЕпривилегированный бот.
# Дефолт обязан совпадать с lib/bot.sh (GODNS_BOT_STATE_DIR=/opt/godns/bot/state)
# и actions.sh: в проде путь задаёт юнит, но тесты и ручные запуски идут без
# него, и разъехавшийся дефолт прятал бы файлы от самого себя.
STATE_DIR = Path(os.environ.get("GODNS_BOT_STATE", "/opt/godns/bot/state"))
USER_LANG_FILE = STATE_DIR / "user_langs.json"

# Маркер языка SSH-меню goDNS: один продукт — один язык по умолчанию.
GODNS_LANG_MARKER = Path(
    os.environ.get("GODNS_DIR", "/opt/godns")) / ".language"

SUPPORTED_LANGS = ("ru", "en")
LANG_NAMES = {"ru": "Русский", "en": "English"}


def _detect_default_lang() -> str:
    try:
        if GODNS_LANG_MARKER.exists():
            code = GODNS_LANG_MARKER.read_text(
                encoding="utf-8").strip()[:2].lower()
            if code in SUPPORTED_LANGS:
                return code
    except OSError as exc:
        logger.warning("cannot read %s: %s", GODNS_LANG_MARKER, exc)
    code = str(os.environ.get("BOT_LANG", "")).strip().lower()
    if code in SUPPORTED_LANGS:
        return code
    return "ru"


DEFAULT_LANG = _detect_default_lang()

_LANG_CACHE: Dict[str, Dict[str, str]] = {}
_USER_LANGS: Dict[int, str] = {}
_USER_LANGS_LOADED = False


def _load_lang_file(code: str) -> Dict[str, str]:
    if code in _LANG_CACHE:
        return _LANG_CACHE[code]
    path = LANG_DIR / f"{code}.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("lang file must contain an object")
    except (OSError, ValueError) as exc:
        logger.warning("lang file %s unusable: %s", path, exc)
        data = {}
    _LANG_CACHE[code] = data
    return data


def _load_user_langs() -> None:
    global _USER_LANGS_LOADED
    if _USER_LANGS_LOADED:
        return
    _USER_LANGS_LOADED = True
    try:
        with open(USER_LANG_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for key, value in raw.items():
            if str(value) in SUPPORTED_LANGS:
                _USER_LANGS[int(key)] = str(value)
    except (OSError, ValueError):
        pass


def get_user_lang(user_id: Optional[int]) -> str:
    _load_user_langs()
    if user_id is not None and user_id in _USER_LANGS:
        return _USER_LANGS[user_id]
    return DEFAULT_LANG


def set_user_lang(user_id: int, code: str) -> bool:
    if code not in SUPPORTED_LANGS:
        return False
    _load_user_langs()
    _USER_LANGS[int(user_id)] = code
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = USER_LANG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {str(k): v for k, v in _USER_LANGS.items()},
            ensure_ascii=False), encoding="utf-8")
        tmp.replace(USER_LANG_FILE)
        return True
    except OSError as exc:
        logger.warning("cannot persist user langs: %s", exc)
        return False


def get_language_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


def t(user_id: Optional[int], key: str) -> str:
    lang = get_user_lang(user_id)
    table = _load_lang_file(lang)
    if key in table:
        return table[key]
    if lang != "ru":
        fallback = _load_lang_file("ru")
        if key in fallback:
            return fallback[key]
    return key


def tf(user_id: Optional[int], key: str, *args) -> str:
    template = t(user_id, key)
    try:
        return template.format(*args)
    except (IndexError, KeyError, ValueError):
        return template

#!/usr/bin/env python3
"""run_bot.py — запустить настоящий bot.py против подставного Bot API (fake_tg_api.py), не меняя продукт.

    GA_FAKE_TG_API=http://127.0.0.1:18081 /opt/goamnezia-bot/venv/bin/python run_bot.py /opt/goamnezia-bot/bot.py

Обёртка только подменяет адрес Bot API у ApplicationBuilder (base_url / base_file_url) и запускает bot.py как
__main__ — всё остальное (.env, мост, очередь уведомлений, привязка) у бота своё. Ставится drop-in'ом к штатной службе
goamnezia-bot.service на время проверки (bot_live.sh), после — drop-in убирается. Адрес — только петля: выдуманный
токен не должен уйти никуда, кроме подставного API на этой же машине.
"""
import os
import re
import runpy
import sys

API = (os.environ.get("GA_FAKE_TG_API") or "").rstrip("/")
if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{2,5}", API):
    sys.stderr.write("run_bot.py: GA_FAKE_TG_API must be http://127.0.0.1:<port> (the local fake Bot API)\n")
    sys.exit(2)

from telegram.ext import ApplicationBuilder  # noqa: E402 — из venv бота

_orig_build = ApplicationBuilder.build


def _build_with_fake_api(self):
    self.base_url(API + "/bot")
    self.base_file_url(API + "/file/bot")
    return _orig_build(self)


ApplicationBuilder.build = _build_with_fake_api

bot_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/goamnezia-bot/bot.py"
sys.argv = [bot_path]
sys.stderr.write("run_bot.py: bot %s → fake Bot API %s\n" % (bot_path, API))
runpy.run_path(bot_path, run_name="__main__")

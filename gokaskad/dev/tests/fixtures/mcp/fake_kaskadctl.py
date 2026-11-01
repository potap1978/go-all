"""Заглушка kaskadctl для tests/test_mcp.py: пишет argv, stdin и KASKAD_CALLER в FAKE_CTL_LOG (строка JSON на вызов)
и отвечает по переменным окружения:

    FAKE_CTL_REPLY   JSON-документ ответа (по умолчанию {"ok": true, "argv": [...]})
    FAKE_CTL_RC      код выхода (0)
    FAKE_CTL_SLEEP   спать N секунд перед ответом (проверка таймаута)
    FAKE_CTL_NOJSON  1 — вместо JSON мусор
    FAKE_CTL_NOISE   1 — строки в stderr и мусор в stdout перед JSON
    FAKE_CTL_DONE    путь: создать файл в самом конце (команду не убили)
"""
import json
import os
import sys
import time

argv = sys.argv[1:]
data = None if sys.stdin is None or sys.stdin.closed else sys.stdin.read()
with open(os.environ["FAKE_CTL_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "stdin": data}, ensure_ascii=False) + "\n")
if os.environ.get("FAKE_CTL_SLEEP"):
    time.sleep(float(os.environ["FAKE_CTL_SLEEP"]))
if os.environ.get("FAKE_CTL_NOISE"):
    sys.stderr.write("Внимание: шумим в stderr\n\x1b[0;33mцвет\x1b[0m\n")
    sys.stdout.write("мусор до JSON\n")
if os.environ.get("FAKE_CTL_NOJSON"):
    sys.stdout.write("это не JSON\n")
else:
    reply = os.environ.get("FAKE_CTL_REPLY")
    sys.stdout.write((reply if reply else json.dumps({"ok": True, "argv": argv}, ensure_ascii=False)) + "\n")
if os.environ.get("FAKE_CTL_DONE"):
    open(os.environ["FAKE_CTL_DONE"], "w").close()
sys.exit(int(os.environ.get("FAKE_CTL_RC") or 0))

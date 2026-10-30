# -*- coding: utf-8 -*-
"""Проверка каждого экрана настоящим Telegram — без единого доставленного сообщения.

Telegram разбирает parse_mode и reply_markup ДО того, как ищет чат: отправка на
несуществующий chat_id=1 возвращает «chat not found», только если разметка
корректна, и текст ошибки разбора — если нет. Значит, можно прогнать весь набор
экранов через настоящий валидатор Telegram, ничего никому не отправив.

Ловит то, чего не видно на стороне бота: незакрытые теги, неэкранированные
`<` и `&` в именах ключей и шаблонов, слишком длинный текст, битую клавиатуру.
"""
import json, sys, time, urllib.parse, urllib.request

TOKEN = sys.argv[1]
API = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
payloads = json.load(open("/tmp/payloads.json"))

def call(text, kb, parse="HTML"):
    body = {"chat_id": "1", "parse_mode": parse, "text": text}
    if kb:
        body["reply_markup"] = json.dumps({"inline_keyboard": [
            [({"text": b["t"], "callback_data": b["c"]} if b["c"]
              else {"text": b["t"], "url": "https://example.com"}) for b in row]
            for row in kb if row]})
    data = urllib.parse.urlencode(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(API, data=data), timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)
    except Exception as exc:
        return {"ok": False, "description": f"сеть: {exc}"}

bad = []
print(f"проверяю {len(payloads)} экранов настоящим Telegram\n")
for i, p in enumerate(payloads, 1):
    r = call(p["text"], p.get("kb"))
    d = str(r.get("description", ""))
    if "chat not found" in d:
        ok = True
    else:
        ok = False
        bad.append((p["cb"], d))
    if i % 20 == 0:
        print(f"  … {i}/{len(payloads)}")
    time.sleep(0.06)

print()
if bad:
    print(f"✗ Telegram отверг {len(bad)} из {len(payloads)}:")
    for cb, d in bad:
        print(f"   {cb:30} {d[:100]}")
else:
    print(f"✓ все {len(payloads)} экранов Telegram принимает: разметка, длина, клавиатуры")

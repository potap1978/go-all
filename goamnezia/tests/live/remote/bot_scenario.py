#!/usr/bin/env python3
"""bot_scenario.py — живая проверка Telegram-бота и мини-аппа как пользователь, через подставной Telegram.

    python3 bot_scenario.py [--skip crawl,netdeps,...]      # на сервере, после `bot_live.sh setup`

Настоящий bot.py (служба goamnezia-bot с drop-in на run_bot.py) и настоящая панель; Telegram — подставной
(fake_tg_api.py на 127.0.0.1:18081, выдуманный токен). Человек — tg_user.py: пишет, жмёт кнопки, читает экраны.
Шаги — по задаче владельца 03.10: привязка (итог bot_live.sh setup), «Пользователи», добавить одного, «Добавить
сразу несколько» кнопкой и своим числом, ключи из карточки (факт, размер, совпадение с мостом), удаление фразой,
чужой и группа, уведомления сторожа (netdeps_dangling, disk_level), мини-апп (вход initData, отказы, приглашённый,
удалённый, массовое добавление запросом панели), обход экранов «только чтение», журнал подставного API.

Своих людей сценарий записывает в created-users.txt (0600) — их и только их удаляет bot_live.sh cleanup; имена из
массового добавления в боте случайные (префикса бот не умеет), в панели — с префиксом bot-ma. Печатает таблицу
«проверка → OK/FAIL → доказательство» (без ключей и секретных путей), код выхода 1 при FAIL.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_user as tg  # noqa: E402
import miniapp_check as mc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CREATED = os.path.join(HERE, "created-users.txt")
BULK = os.path.join(HERE, "bulk-users.txt")
OWNER, STRANGER, INVITED = tg.PEOPLE["owner"][0], tg.PEOPLE["stranger"][0], tg.PEOPLE["invited"][0]
RESULTS = []


def res(ok, name, evidence):
    RESULTS.append((bool(ok), name, str(evidence)))
    print("%-4s %s — %s" % ("OK" if ok else "FAIL", name, evidence), flush=True)


def bridge(*args, stdin=None):
    p = subprocess.run(["bash", "/opt/goamnezia/install.sh", *args, "--json"], capture_output=True, text=True,
                       input=stdin, env={**os.environ, "GOAMNEZIA_WHO": "live-bot-scenario"})
    for line in reversed((p.stdout or "").strip().splitlines()):
        try:
            d = json.loads(line)
            if isinstance(d, dict) and "status" in d:
                return d
        except ValueError:
            continue
    return {"status": "error", "code": "no_json"}


def remember(names):
    fd = os.open(CREATED, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as fh:
        for n in names:
            fh.write(n + "\n")


def users():
    return {u["name"]: u for u in (bridge("--action=user-list").get("users") or [])}


def sends(evs, who=OWNER):
    return [e for e in evs if e.get("kind") == "send" and e.get("chat") == tg.who_id(who)
            and e.get("method") in ("sendMessage", "editMessageText")]


def last_screen(evs, who=OWNER):
    s = sends(evs, who)
    return s[-1] if s else {}


def press(label, who="owner", until=None, wait=60, mid=None, data=None):
    r = tg.press(who, label, mid=mid, data=data)
    return tg.settle(r["since"], cb=r["callback_id"], timeout=wait, until=until, who=who)


def say(text, who="owner", until=None, wait=60):
    r = tg.send(who, text)
    return tg.settle(r["since"], timeout=wait, until=until, who=who)


def latest_mid(who="owner"):
    msgs = [m for m in tg.bot_messages(who) if m.get("reply_markup")]
    return msgs[-1]["mid"] if msgs else None


def step(fn):
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit as e:
            res(False, fn.__name__, "остановка: %s" % e)
        except Exception as e:  # noqa: BLE001 — сбой шага — FAIL в таблице, не обрыв сценария
            res(False, fn.__name__, "%s: %s" % (type(e).__name__, str(e)[:200]))
            traceback.print_exc(limit=2)
    run.__name__ = fn.__name__
    return run


# ── 1. Привязка (сделана в bot_live.sh setup) ────────────────────────────────────────────────────────────────────────
@step
def pairing():
    done = os.path.join(HERE, "pair.done")
    d = json.load(open(done)) if os.path.exists(done) else {}
    env = open("/opt/goamnezia-bot/.env", encoding="utf-8").read()
    ids = re.search(r"(?m)^ALLOWED_IDS=(.*)$", env)
    owner = re.search(r"(?m)^OWNER_ID=(.*)$", env)
    res(d.get("rc") == 0 and len(d.get("steps_done") or []) == 2 and ids and str(OWNER) in ids.group(1)
        and owner and owner.group(1).strip() == str(OWNER),
        "привязка владельца сообщением: SSH (install_bot.sh --pair) → «1» → «1»",
        "pty: rc=%s, шаги %s; .env ALLOWED_IDS=%s OWNER_ID=%s" % (d.get("rc"), [s.get("step") for s in d.get("steps_done") or []],
                                                                ids.group(1) if ids else "?", owner.group(1) if owner else "?"))
    chat = tg.chat("owner")
    paired = [m for m in chat if m["sender"] == "bot" and "администратор сервера" in m["plain"]]
    stranger_first = [m for m in chat if m["sender"] == "bot" and "Это бот частного сервера" in m["plain"]]
    res(bool(stranger_first) and bool(paired), "до окна — ответ «бот частного сервера»; после — «✓ Вы администратор сервера»",
        "ответов чужому: %d; приветствие: %s" % (len(stranger_first), paired[0]["text_safe"].splitlines()[0] if paired else "нет"))
    evs = say("/start")
    scr = last_screen(evs)
    btns = json.dumps(scr.get("buttons") or [], ensure_ascii=False)
    res("Выберите раздел" in (scr.get("text") or "") and "web_app" in btns and "Панель" in btns,
        "главный экран владельца с кнопкой «🚀 Панель» (Web App)", (scr.get("text_safe") or "").splitlines()[0][:60])
    st = tg.call("/_test/state")
    mb = st["menu_buttons"].get(str(OWNER)) or {}
    tg.call("/_test/webapp", query={"user": OWNER, "source": "menu"}, raw=True)
    ev = [e for e in tg.call("/_test/events", query={"since": 0})["events"] if e.get("action") == "webapp_open"][-1]
    res(mb.get("type") == "web_app" and ev.get("button_is_panel") and (st["menu_buttons"].get("default") or {}).get("type") == "commands",
        "кнопка меню чата: владельцу — Web App панели, всем остальным — команды",
        "владельцу: %s → %s (= панель из config.json: %s); общая: %s" % (mb.get("type"), mb.get("url_safe"), ev.get("button_is_panel"),
                                                                       (st["menu_buttons"].get("default") or {}).get("type")))


# ── 2. Пользователи ──────────────────────────────────────────────────────────────────────────────────────────────────
@step
def users_list():
    evs = say("/users")
    scr = last_screen(evs)
    text = scr.get("text") or ""
    res("Пользователи" in text and "u_view~" in json.dumps(scr.get("buttons") or []), "/users — список с карточками",
        "людей в мосте: %d" % len(users()))
    first = next((b for row in (scr.get("buttons") or []) for b in row if "u_view~" in b), "")
    name = first.split("u_view~")[-1] if first else ""
    evs = press(re.escape(first.split(" → ")[0]) if first else "^$", mid=scr.get("mid"))
    card = last_screen(evs)
    res(card and ("👤 %s" % name) in (card.get("text") or "") and "u_key~" in json.dumps(card.get("buttons") or []),
        "карточка из списка: профили и кнопки ключей", (card.get("text_safe") or "").splitlines()[0][:60])


@step
def add_one():
    name = "bot-single"
    if name in users():
        bridge("--action=user-del", "--name=" + name)
    evs = press("", mid=latest_mid(), data="u_add")
    assert "Новый пользователь" in (last_screen(evs).get("text") or ""), "нет экрана имени"
    evs = say(name)
    assert "профилях" in (last_screen(evs).get("text") or ""), "нет экрана профилей"
    set_profiles({"awg2", "awg3"})
    press("^✅ Готово$")
    press("^Без лимита$")
    evs = press("^Бессрочно$", until=r"создан|❌", wait=120)
    remember([name])
    u = users().get(name) or {}
    done = [e for e in sends(evs) if "создан" in (e.get("text") or "")]
    res(done and sorted(u.get("profiles") or []) == ["awg2", "awg3"] and not u.get("quota_bytes") and not u.get("expires"),
        "«➕ Добавить пользователя»: имя текстом → профили → без лимита → бессрочно",
        "%s; мост: профили %s, лимит %s, срок %s" % (done[-1]["text_safe"].splitlines()[0] if done else "нет итога",
                                                   ",".join(u.get("profiles") or []), u.get("quota_bytes"), u.get("expires") or "—"))


def bulk_names(text):
    return re.findall(r"<code>([A-Za-z0-9_-]+)</code>", text or "")


def set_profiles(want):
    """Галочки профилей на последнем экране — ровно want (awg2, awg3…): на стенде может появиться чужой awg1."""
    for _ in range(6):
        m = tg.bot_messages("owner")[-1]
        rows = (m.get("reply_markup") or {}).get("inline_keyboard") or []
        wrong = [b for row in rows for b in row if re.match(r"^(ua|ub)_tog~", b.get("callback_data", ""))
                 and (b["text"].startswith("☑") != (b["callback_data"].split("~")[1] in want))]
        if not wrong:
            return
        press("", mid=m["mid"], data=wrong[0]["callback_data"])


@step
def bulk_button():
    before = set(users())
    evs = press("", mid=latest_mid(), data="u_addm")
    assert "Сразу несколько" in (last_screen(evs).get("text") or "")
    evs = press("^5$")
    assert "Человек: 5" in (last_screen(evs).get("text") or ""), "нет экрана профилей"
    set_profiles(set())
    evs = press("Добавить: 5")
    alert = [e.get("text_safe") for e in evs if e.get("method") == "answerCallbackQuery" and e.get("show_alert")]
    res(bool(alert) and "профиль" in alert[0], "без отмеченных профилей «Добавить» не зовёт ядро", "алерт: %s" % (alert[0] if alert else "нет"))
    set_profiles({"awg2", "awg3"})
    t0 = time.time()
    evs = press("Добавить: 5", until=r"Добавлено человек|❌", wait=300)
    done = [e for e in sends(evs) if "Добавлено человек" in (e.get("text") or "")]
    text = done[-1]["text"] if done else ""
    names = bulk_names(text)
    remember(names)
    with open(BULK, "w") as fh:
        fh.write("\n".join(names) + "\n")
    os.chmod(BULK, 0o600)
    after = users()
    new = set(after) - before
    flags = done[-1].get("flags") if done else {}
    res(len(names) == 5 and set(names) == new and all(sorted(after[n]["profiles"]) == ["awg2", "awg3"] for n in names),
        "«👥 Добавить сразу несколько»: 5 кнопкой, обе линии, случайные имена",
        "за %.0f с; %s; в мосте новых %d, у всех 2.0+3.1" % (time.time() - t0, ", ".join(names), len(new)))
    labels = re.findall(r"• (.+?) — <code>", text)
    res(len(done) == 1 and not any(flags.values()) and len(labels) == 5 and all(re.search("[а-яА-ЯёЁ]", l) for l in labels)
        and "карточке каждого" in text,
        "итог одним сообщением, без ключей; подписи — на языке человека (ru)",
        "сообщений с итогом: %d; ключей/секретов в тексте: %s; подписи: %s" % (len(done), [k for k, v in (flags or {}).items() if v] or "нет",
                                                                          ", ".join(labels[:3]) + "…"))


@step
def bulk_text():
    before = set(users())
    evs = press("", mid=latest_mid(), data="u_addm")
    bad = []
    for wrong in ("0", "101", "три"):
        evs = say(wrong)
        bad.append((last_screen(evs).get("text") or "").strip())
    res(all("от 1 до 100" in b for b in bad), "своё число: 0, 101, «три» — отказ без потери шага", " | ".join(sorted(set(bad))))
    evs = say("3")
    assert "Человек: 3" in (last_screen(evs).get("text") or ""), "нет экрана профилей после «3»"
    set_profiles({"awg2"})
    evs = press("Добавить: 3", until=r"Добавлено человек|❌", wait=300)
    done = [e for e in sends(evs) if "Добавлено человек" in (e.get("text") or "")]
    names = bulk_names(done[-1]["text"] if done else "")
    remember(names)
    after = users()
    res(len(names) == 3 and set(names) == set(after) - before and all(after[n]["profiles"] == ["awg2"] for n in names)
        and "(AmneziaWG 2.0)" in (done[-1]["text"] if done else ""),
        "«Добавить сразу несколько»: 3 текстом, только 2.0", "%s; в мосте у всех только awg2" % ", ".join(names))


@step
def users_pages():
    """После массовых добавлений людей больше страницы: «Страница N из M · всего: K» — K должно быть числом людей."""
    total = len(users())
    evs = say("/users")
    p1 = last_screen(evs)
    m1 = re.search(r"Страница (\d+) из (\d+) · всего: (\d+)", p1.get("text") or "")
    m2 = None
    if m1 and int(m1.group(2)) > 1:
        evs = press("^›$", mid=p1.get("mid"))
        m2 = re.search(r"Страница (\d+) из (\d+) · всего: (\d+)", last_screen(evs).get("text") or "")
    if not m1:
        res(total <= 8, "список на нескольких страницах", "страниц нет, людей %d" % total)
        return
    shown = [int(m1.group(3))] + ([int(m2.group(3))] if m2 else [])
    res(all(x == total for x in shown), "список: «всего» на каждой странице = людей на сервере",
        "стр. 1: «всего: %s», стр. 2: «всего: %s»; людей в мосте: %d (bot.py users_list_text получает срез страницы — len(chunk))"
        % (m1.group(3), m2.group(3) if m2 else "—", total))


@step
def keys_from_card():
    names = [n.strip() for n in open(BULK) if n.strip()]
    name = names[0]
    evs = say("/key %s" % name)
    evs = press("Открыть карточку")
    card = last_screen(evs)
    assert ("👤 %s" % name) in (card.get("text") or ""), "карточка не открылась"
    got = []
    for prof, line in (("awg3", "3.1"), ("awg2", "2.0")):
        for fmt, label in (("conf", "файл .conf"), ("vpn", "vpn://"), ("qrconf", "QR .conf"), ("qrvpn", "QR vpn://")):
            evs = press("^%s · %s$" % (re.escape(line), re.escape(label)), mid=card["mid"], wait=60)
            if fmt == "conf":
                d = [e for e in evs if e.get("method") == "sendDocument"]
                conf = bridge("--action=user-key", "--name=" + name, "--profile=" + prof, "--format=conf").get("conf") or ""
                hashes = {hashlib.sha256(x.encode()).hexdigest()[:16] for x in (conf, conf + "\n", conf.rstrip("\n") + "\n")}
                ok = d and d[-1]["file"]["name"] == "%s-%s.conf" % (name, prof) and d[-1]["file"]["sha256"] in hashes
                got.append((ok, "%s .conf документом %s Б%s" % (line, d[-1]["file"]["size"] if d else "?", ", = мосту" if ok else "")))
            elif fmt == "vpn":
                msg = [m for m in tg.bot_messages("owner") if m["flags"].get("vpn_link")]
                link = re.search(r"vpn://[A-Za-z0-9_\-+/=.]+", msg[-1]["plain"]).group(0) if msg else ""
                ok = bool(link) and link == (bridge("--action=user-key", "--name=" + name, "--profile=" + prof, "--format=vpn").get("vpn") or "")
                pv = [e for e in evs if e.get("method") == "sendMessage" and (e.get("flags") or {}).get("vpn_link")]
                got.append((ok and pv and pv[-1].get("preview_disabled"), "%s vpn:// текстом %d знаков без превью%s" % (line, len(link), ", = мосту" if ok else "")))
            elif fmt == "qrconf":
                p = [e for e in evs if e.get("method") == "sendPhoto"]
                got.append((bool(p) and p[-1]["photo"]["png"], "%s QR .conf PNG %s Б" % (line, p[-1]["photo"]["size"] if p else "?")))
            else:
                g = [e for e in evs if e.get("method") in ("sendMediaGroup", "sendPhoto")]
                n = sum(len(e.get("photos") or [1]) for e in g)
                got.append((n >= 1, "%s QR vpn:// %d фото" % (line, n)))
    res(all(ok for ok, _ in got), "ключи из карточки массово добавленного %s (документ, текст, QR — факт и размер)" % name,
        "; ".join(t for _, t in got))


@step
def delete_phrase():
    name = "bot-single"
    evs = say("/key %s" % name)
    evs = press("Открыть карточку")
    evs = press("Удалить", mid=last_screen(evs).get("mid"))
    assert "DELETE %s" % name in (last_screen(evs).get("text") or ""), "нет вопроса с фразой"
    replies = []
    for wrong in ("DELETE bot-singl", "delete %s" % name, "DELETE %s" % (open(BULK).readline().strip())):
        evs = say(wrong)
        replies.append((last_screen(evs).get("text") or "").split(".")[0])
    still = name in users()
    res(still and all("не совпала" in r for r in replies), "неверная фраза не удаляет (опечатка, регистр, чужое имя)",
        "ответы: %s; %s на месте: %s" % (sorted(set(replies)), name, still))
    evs = say("DELETE %s" % name, until=r"удал[её]н|❌", wait=120)
    gone = name not in users()
    done = [e for e in sends(evs) if "удал" in (e.get("text") or "")]
    res(gone and done, "верная фраза «DELETE %s» удаляет" % name, "%s; мост: %s" % (done[-1]["text_safe"] if done else "нет итога",
                                                                                   "нет такого" if gone else "ещё есть"))


@step
def strangers():
    evs = say("/start", who="stranger", wait=20)
    t = (last_screen(evs, STRANGER).get("text_safe") or "")
    evs2 = say("/users", who="stranger", wait=20)
    mid = [m["mid"] for m in tg.bot_messages("stranger")][-1]
    evs3 = press("", who="stranger", mid=mid, data="u_del~%s" % open(BULK).readline().strip(), wait=15)
    alert = [e.get("text_safe") for e in evs3 if e.get("method") == "answerCallbackQuery" and e.get("text_safe")]
    r = tg.send("owner", "/start", chat_id=-1001234567, chat_type="group")
    evs4 = tg.settle(r["since"], timeout=12)
    group_out = [e for e in evs4 if e.get("kind") == "send" and e.get("chat") == -1001234567]
    res("частного сервера" in t and str(STRANGER) in t and alert and not group_out and open(BULK).readline().strip() in users(),
        "чужой: ответ без меню, подделанная кнопка — «Доступ запрещён»; группа — тишина",
        "чужому: «%s…»; алерт: %s; в группу отправлено: %d" % (t.split("\n")[0][:40], alert[0] if alert else "нет", len(group_out)))


@step
def netdeps():
    subprocess.run(["docker", "rm", "-f", "bot-netdeps-host", "bot-netdeps-nbr"], capture_output=True)
    since = tg.seq_now()
    subprocess.run(["docker", "run", "-d", "--name", "bot-netdeps-host", "--label", "ga-live=bot", "alpine:3", "sleep", "7200"],
                   check=True, capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", "bot-netdeps-nbr", "--label", "ga-live=bot", "--network",
                    "container:bot-netdeps-host", "alpine:3", "sleep", "7200"], check=True, capture_output=True)
    subprocess.run(["docker", "rm", "-f", "bot-netdeps-host"], check=True, capture_output=True)
    bridge("--action=watch-tick")
    time.sleep(40)
    msgs = [e for e in tg.call("/_test/events", query={"since": since})["events"]
            if e.get("method") == "sendMessage" and "bot-netdeps-nbr" in (e.get("text") or "")]
    since2 = tg.seq_now()
    bridge("--action=watch-tick")
    time.sleep(35)
    again = [e for e in tg.call("/_test/events", query={"since": since2})["events"]
             if e.get("method") == "sendMessage" and "bot-netdeps-nbr" in (e.get("text") or "")]
    subprocess.run(["docker", "rm", "-f", "bot-netdeps-nbr"], capture_output=True)
    bridge("--action=watch-tick")
    mark = os.path.exists("/opt/goamnezia/state/.netdeps/dangling.reported")
    own = [e for e in msgs if e.get("chat") == OWNER]
    res(len(own) == 1 and "Сторож" in own[0]["text"] and "{" not in own[0]["text_safe"] and not again and not mark,
        "уведомление netdeps_dangling: своим текстом, ровно один раз; после уборки соседа отметка снята",
        "«%s…»; повтор после второго тика: %d; отметка: %s" % (own[0]["text_safe"][:70] if own else "нет", len(again),
                                                                "осталась" if mark else "снята"))


@step
def disk_level():
    since = tg.seq_now()
    with open("/run/goamnezia/disk_level", "w") as fh:
        fh.write("low\n")
    bridge("--action=watch-tick")
    time.sleep(40)
    msgs = [e for e in tg.call("/_test/events", query={"since": since})["events"]
            if e.get("method") == "sendMessage" and "Места на диске снова достаточно" in (e.get("text") or "")]
    to = sorted({e["chat"] for e in msgs})
    admins = sorted(int(x) for x in re.search(r"(?m)^ALLOWED_IDS=(.*)$", open("/opt/goamnezia-bot/.env").read()).group(1).split(",") if x.strip())
    res(to == admins, "уведомление disk_level (возврат в ok после «low» в памяти сторожа) — каждому админу",
        "получили: %s; админы: %s" % (to, admins))


# ── Мини-апп ────────────────────────────────────────────────────────────────────────────────────────────────────────
@step
def miniapp():
    domain, base = mc.panel_base()
    api = tg.API
    for who, mode, want, label in (("stranger", "good", "miniapp_not_admin", "чужой id"), ("owner", "badsig", "signature_invalid", "неверная подпись"),
                                   ("owner", "othertoken", "signature_invalid", "подпись чужим токеном"), ("owner", "stale", "miniapp_expired", "подпись старше суток")):
        code, hdrs, data = mc.login(domain, base, api, who, mode)
        res(code == 401 and data.get("code") == want and "ga_session=" not in hdrs, "мини-апп: %s → 401" % label,
            "HTTP %s, code=%s" % (code, data.get("code")))
    jar = os.path.join(HERE, "state", "owner.jar")
    fd = os.open(jar, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    code, hdrs, data = mc.login(domain, base, api, "owner", jar=jar)
    c2, _h, sess = mc.curl(domain, base + "/api/session", headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
    sd = sess.get("data") or {}
    setc = next((l.lower() for l in hdrs.splitlines() if "ga_session=" in l), "")
    res(code == 200 and data.get("ticket") and sd.get("who") == "tg:%d" % OWNER and "samesite=none" in setc and "secure" in setc,
        "мини-апп: вход владельца по initData через домен (nginx) → сессия", "HTTP %s; /api/session who=%s state=%s; cookie Secure, SameSite=None" % (
            code, sd.get("who"), sd.get("state")))
    # Массовое добавление настоящим запросом панели из сессии мини-аппа (префикс bot-ma)
    before = set(users())
    c3, _h, r = mc.curl(domain, base + "/api/action/users-add-bulk", "POST",
                        {"count": "3", "profiles": "awg3,awg2", "prefix": "bot-ma", "lang": "ru"}, {"X-GoAmnezia-Admin": "1"}, jar)
    added = [a.get("name") for a in ((r.get("data") or {}).get("added") or [])]
    remember(added)
    keys_ok = []
    for n in added[:1]:
        c4, _h, k = mc.curl(domain, base + "/api/users/%s/key?profile=awg2&format=all" % n, headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
        kd = k.get("data") or k
        keys_ok.append(c4 == 200 and "[Interface]" in str(kd.get("conf") or "") and str(kd.get("vpn") or "").startswith("vpn://"))
    audit = subprocess.run(["grep", "users-add-bulk", "/var/log/goamnezia-audit.log"], capture_output=True, text=True).stdout.strip().splitlines()
    res(c3 == 200 and len(added) == 3 and set(added) == set(users()) - before and all(keys_ok) and audit and "tg:%d" % OWNER in audit[-1],
        "мини-апп: «Добавить несколько» запросом панели из сессии мини-аппа → люди есть, ключ открывается",
        "HTTP %s; %s; ключ 2.0 (conf + vpn://) по API карточки: %s; аудит: who=panel:tg:%d" % (c3, ", ".join(added), all(keys_ok), OWNER))
    os.remove(jar)


@step
def invited_admin():
    domain, base = mc.panel_base()
    code, _h, data = mc.login(domain, base, tg.API, "invited")
    res(code == 401 and data.get("code") == "miniapp_not_admin", "мини-апп: будущий приглашённый до /invite → 401", "HTTP %s, %s" % (code, data.get("code")))
    evs = say("/invite %d" % INVITED)
    evs2 = say("/start", who="invited")
    scr = last_screen(evs2, INVITED)
    code, _h, data = mc.login(domain, base, tg.API, "invited")
    res(code == 200 and "Выберите раздел" in (scr.get("text") or ""), "/invite владельца → приглашённый видит меню и входит в мини-апп",
        "%s; мини-апп HTTP %s" % ((last_screen(evs).get("text_safe") or "")[:60], code))
    evs3 = say("/invite %d" % STRANGER, who="invited")
    t = last_screen(evs3, INVITED).get("text") or ""
    res("только владелец" in t, "приглашённый не раздаёт доступ дальше (/invite — только владелец)", t.split(".")[0][:70])
    mb = tg.call("/_test/state")["menu_buttons"].get(str(INVITED)) or {}
    res(mb.get("type") == "web_app", "приглашённому ставится кнопка меню «🚀 Панель» (как при привязке)",
        "кнопка меню чата приглашённого: %s (bot.py cmd_invite не зовёт refresh_menu_button; ставится только при рестарте бота)" % (mb.get("type") or "не ставилась"))
    # Сессия приглашённого и его удаление владельцем из «Настройки → Администраторы»
    jar = os.path.join(HERE, "state", "invited.jar")
    fd = os.open(jar, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    mc.login(domain, base, tg.API, "invited", jar=jar)
    evs = press("", mid=latest_mid(), data="se_admins")
    evs = press("Удалить %d" % INVITED)
    evs = press("Подтвердить")
    env = open("/opt/goamnezia-bot/.env").read()
    mb2 = tg.call("/_test/state")["menu_buttons"].get(str(INVITED)) or {}
    code, _h, data = mc.login(domain, base, tg.API, "invited")
    evs4 = say("/start", who="invited")
    res(str(INVITED) not in re.search(r"(?m)^ALLOWED_IDS=(.*)$", env).group(1) and mb2.get("type") == "commands" and code == 401
        and "частного сервера" in (last_screen(evs4, INVITED).get("text") or ""),
        "удаление приглашённого в боте: .env, кнопка меню сброшена, бот и новый вход в мини-апп — отказ",
        "кнопка: %s; новый вход: HTTP %s %s" % (mb2.get("type"), code, data.get("code")))
    c5, _h, d5 = mc.curl(domain, base + "/api/overview", headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
    res(c5 == 401, "открытая сессия мини-аппа удалённого админа закрывается",
        "после удаления /api/overview по прежней cookie: HTTP %s (server.py session_valid не сверяет tg:<id> с ALLOWED_IDS; "
        "сессия живёт до 15 мин)" % c5)
    mc.curl(domain, base + "/api/auth/logout", "POST", {}, {"X-GoAmnezia-Admin": "1"}, jar)
    os.remove(jar)


@step
def crawl():
    p = subprocess.run([sys.executable, os.path.join(HERE, "bot_crawl.py"), "--as", "owner", "--depth", "3", "--users", "2"],
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout.strip().splitlines()
    tail = [l for l in out if l.startswith("экранов:")]
    red = [l.split("→")[0].split("]")[-1].strip() for l in out if "❌ на экране" in l]
    with open(os.path.join(HERE, "crawl.txt"), "w") as fh:
        fh.write(p.stdout)
    os.chmod(os.path.join(HERE, "crawl.txt"), 0o600)
    res(p.returncode == 0, "обход экранов «только чтение» (белый список кнопок): отказов API и нажатий без ответа нет",
        "%s; экраны с «❌»: %s" % (tail[0] if tail else "нет итога", ", ".join(red) or "нет"))


@step
def api_log():
    evs = tg.call("/_test/events", query={"since": 0})["events"]
    errs = [(e.get("method"), e.get("error")) for e in evs if e.get("kind") in ("error", "fake_crash")
            and "message is not modified" not in str(e.get("error"))]
    warns = [w for e in evs for w in (e.get("warn") or [])]
    late = [e for e in evs if e.get("method") == "answerCallbackQuery" and ((e.get("after") or 0) > 5 or e.get("ok") is False)]
    unknown = tg.call("/_test/state")["unknown_methods"]
    res(not errs and not warns and not late and not unknown,
        "журнал подставного Telegram: ни отказа API (разметка, длина, кнопки), ни поздних ответов, ни неизвестных методов",
        "событий %d; отказы %s; предупреждения %s; поздние ответы %d; неизвестные методы %s" % (len(evs), errs or "нет", warns or "нет", len(late), unknown or "нет"))


STEPS = [pairing, users_list, add_one, bulk_button, bulk_text, users_pages, keys_from_card, delete_phrase, strangers, netdeps,
         disk_level, miniapp, invited_admin, crawl, api_log]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="", help="шаги через запятую: " + ",".join(s.__name__ for s in STEPS))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    skip = {x for x in a.skip.split(",") if x}
    only = {x for x in a.only.split(",") if x}
    ver = open("/opt/goamnezia/lib/common.sh").read()
    v = re.search(r'(?m)^GOAMNEZIA_VERSION="([^"]+)"', ver)
    print("goAmnezia %s, бот — подставной Telegram (fake_tg_api.py), владелец %d" % (v.group(1) if v else "?", OWNER), flush=True)
    for s in STEPS:
        if s.__name__ in skip or (only and s.__name__ not in only):
            continue
        print("\n== %s" % s.__name__, flush=True)
        s()
    v2 = re.search(r'(?m)^GOAMNEZIA_VERSION="([^"]+)"', open("/opt/goamnezia/lib/common.sh").read())
    if v and v2 and v.group(1) != v2.group(1):
        res(False, "версия кода на сервере не менялась по ходу проверки", "%s → %s" % (v.group(1), v2.group(1)))
    print("\n| # | проверка | итог | доказательство |\n|---|---|---|---|")
    for i, (ok, name, ev) in enumerate(RESULTS, 1):
        print("| %d | %s | %s | %s |" % (i, name, "OK" if ok else "FAIL", ev.replace("|", "/")))
    n_ok = sum(1 for r in RESULTS if r[0])
    print("\nИТОГ: OK %d, FAIL %d" % (n_ok, len(RESULTS) - n_ok))
    sys.exit(0 if n_ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

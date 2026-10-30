#!/usr/bin/env python3
"""bot_crawl.py — обход экранов бота «только чтение» через подставной Telegram (fake_tg_api.py): на сервере, под root.

    python3 bot_crawl.py [--as owner] [--depth 3] [--users 2]

От главного меню нажимает кнопки из белого списка (экраны и вопросы, без единой мутации: ни создания копий, ни смены
режима, ни выдачи ключей, ни фраз подтверждения) и на каждом экране смотрит то, что ловит только «настоящий» Telegram:
отказ API (HTML-разметка, длина, callback_data), предупреждения подставного API, всплывающие ответы, экраны с «❌» и
нажатия без ответа. Печатает строку на экран (текст — очищенный) и итог; код выхода 1 — был отказ API или нажатие без
ответа. Пользователей в списке открывает не больше --users.
"""
import argparse
import re
import sys

import tg_user as tg

SAFE_EXACT = {
    "menu_main", "menu_users", "sec_state", "st_status", "st_traffic", "st_logs", "st_addr", "sec_protocols", "sec_maint",
    "mt_upstream", "mt_import", "mt_diag", "sh_list", "sec_settings", "se_mode", "se_mode_pick", "se_tpl", "se_check",
    "se_alt", "se_pf", "se_dns", "se_endpoint", "se_panel", "se_agents", "se_mirror", "se_admins", "menu_lang", "se_about",
    "mt_restore", "mt_restart_all", "mr_fetch", "se_pline",
}
# Экраны по префиксу: карточки, списки, вопросы (подтверждение — следующим нажатием, его здесь нет)
SAFE_PREFIX = ("menu_users~", "u_view~", "pr_card~", "pr_params~", "st_log~", "pg_list~", "pg_card~", "ag_view~", "u_kaddr~",
               "u_kah~", "u_pin~", "u_share~", "u_shf~", "u_page~", "u_quota~", "u_exp~", "u_addp~", "u_delp~", "pr_mtu~",
               "pr_restart~", "pr_fix~", "pr_preset~", "se_tplc~", "mr_app~")
NEVER = re.compile(r"(_do(~|$)|^u_key~|^u_ka~|^u_shdo~|^pg_ttl~|^pg_url~|^pg_rev|^u_del~|^u_en~|^uq_set~|^ue_set~|"
                   r"^u_delp_ask~|^u_reis|^u_att~|^pr_inst|^pr_remove~|^pr_port|^pr_443|^pr_subnet~|^pr_mtu_set~|^pr_pedit~|"
                   r"^pr_pkey~|^pr_regen~|^mt_rs~|^se_dns_set~|^ag_(?!view~)|^se_admin_del|^se_mode_(to|nodom|keep|pl)~|"
                   r"^se_pline~|^se_tplf~|^se_tplp~|^se_alt_set~|^se_mini~|^lang_|^mt_backup$|^sh_rev|^mr_fetch~|^mr_del|"
                   r"^imp_|^up_|^tc_cancel~|^inst_awg2~|^menu_install$|^close_menu$|^se_wipe$|^se_lazy|^admin_|^noop$|"
                   r"^u_ren~|^ua_|^ub_|^u_add|^mt_selfupdate$|^se_endpoint_(ip|auto)$|^se_dns_custom$|^se_tplq$|^se_tplr$)")


def safe(data: str) -> bool:
    if NEVER.search(data):
        return False
    return data in SAFE_EXACT or data.startswith(SAFE_PREFIX)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="who", default="owner")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--users", type=int, default=2)
    ap.add_argument("--wait", type=float, default=150.0)
    a = ap.parse_args()
    who = a.who
    r = tg.send(who, "/start")
    evs = tg.settle(r["since"], who=who, timeout=60)
    start = [e for e in evs if e.get("method") == "sendMessage" and e.get("buttons")]
    if not start:
        sys.exit("no main menu")
    main_mid = start[-1]["mid"]
    seen, queue, users_opened, tpl_pages = set(), [], 0, 0
    problems = []

    def enqueue(buttons, depth):
        nonlocal users_opened, tpl_pages
        for row in buttons or []:
            for b in row:
                m = re.match(r".* → (\S+)$", b)
                if not m:
                    continue
                data = m.group(1)
                if data in seen or not safe(data):
                    continue
                if data.startswith("u_view~"):
                    if users_opened >= a.users:
                        continue
                    users_opened += 1
                if data.startswith("se_tplc~"):
                    if tpl_pages >= 2:
                        continue
                    tpl_pages += 1
                seen.add(data)
                queue.append((data, depth))

    enqueue(start[-1]["buttons"], 1)
    n = 0
    while queue:
        data, depth = queue.pop(0)
        n += 1
        pr = tg.press(who, "", mid=main_mid, data=data)
        evs = tg.settle(pr["since"], cb=pr["callback_id"], timeout=a.wait, who=who)
        errs = [e for e in evs if e.get("kind") in ("error", "fake_crash")]
        warns = [w for e in evs for w in (e.get("warn") or [])]
        answered = bool(tg.call("/_test/callback", query={"id": pr["callback_id"]}).get("answered"))
        screens = [e for e in evs if e.get("method") in ("editMessageText", "sendMessage") and e.get("chat") == tg.who_id(who)]
        alerts = [e.get("text_safe") for e in evs if e.get("method") == "answerCallbackQuery" and e.get("text_safe")]
        last = screens[-1] if screens else None
        if last and last.get("method") == "sendMessage":
            main_mid = last["mid"]          # экран пришёл новым сообщением — дальше жмём на нём
        head = (last.get("text_safe") or "").splitlines()[0][:70] if last else "—"
        red = bool(last and re.search(r"(^|\n)\s*❌", last.get("text") or ""))
        flags = sorted(k for e in screens for k, v in (e.get("flags") or {}).items() if v)
        mark = "OK  " if answered and not errs else "FAIL"
        if errs or not answered:
            problems.append((data, [x.get("error") for x in errs], answered))
        print("%s [%d] %-28s → %s%s%s%s%s" % (mark, depth, data[:28], head, " | alert: %s" % alerts[0][:60] if alerts else "",
                                             " | ❌ на экране" if red else "", " | WARN %s" % warns if warns else "",
                                             " | флаги %s" % flags if flags else ""), flush=True)
        for e in errs:
            print("      !! %s → %s" % (e.get("method"), e.get("error")))
        if last and depth < a.depth:
            enqueue(last.get("buttons"), depth + 1)
    print("\nэкранов: %d, проблем: %d" % (n, len(problems)))
    for p in problems:
        print("  ", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

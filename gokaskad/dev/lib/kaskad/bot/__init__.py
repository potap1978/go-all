"""Telegram bot of Kaskad 3 on Python (docs/k3/SPEC-wave3.md §4) — replaces the bash bot of install.sh.

    telegram.py    Bot API over kaskad.net.socks (BOT_PROXY), HTML escaping, ApiError/NetworkError
    config.py      /etc/kaskad/config (KEY="value", never sourced) and MENU_STYLE writes
    store.py       bot_state/: dialogs with TTL (<chat>.json), offset.json, strangers.json, small JSON stores
    scheduler.py   a pool of workers: per-chat lanes (one item of a chat at a time) and jobs (1 per chat)
    app.py         receiver (long polling), access control, stale updates, dispatch, Context
    registry.py    callback/command/reply-text/dialog-state handlers registered by the handler modules
    menus.py       keyboards, texts, pagination, labels
    lang.py        the language: the server's KASKAD_LANG for every update and alert, «Язык / Language», tr()
    handlers_main.py   commands, main menu, system, hosting, menu style
    handlers_fwd.py    forwards: dialogs (AWG/VLESS/MTProto/Custom), list, delete by key, flush
    handlers_tools.py  ping (1×/10×/60 s) and the monitoring screens
    gateway.py     the gateway: status, people and devices, issue (files, QR, links), exits, panel login link
    monitor.py     the monitoring thread (monitors/<ip>.conf of 2.x) and the gateway alerts of live.json
    probe.py       GeoIP (ip-api.com) and pings (ICMP via ping(8), TCP connect)
    aliases.py     /etc/kaskad/aliases (IP=name|note|country|isp)
    sysinfo.py     «Система»: uptime, CPU over a real 0.5 s sample, RAM, swap, disk, top processes
    cli.py         kaskadctl bot run | status | test | send-test

The formats of 2.x stay (``config``, ``aliases``, ``monitors/*.conf``, ``bot_state/``): 2.13 and the bash bot
read them; the files of the bash bot in bot_state/ (``<chat>``, ``.offset``, ``system``) are never touched.
"""

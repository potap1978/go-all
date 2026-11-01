# Kaskad SSH menu and installer texts: area core (language, common menu prompts). English.
# Format: M_en_<KEY>='text' — one line, text in single quotes. An apostrophe in words is typographic ’ (can’t,
# server’s — 3.3.2); a single quote that is code (a shell command in the text) is written as the four characters '\''
# (close, backslash-quote, reopen): journalctl -u '\''kaskad-gw-*'\''.
# Double quotes, $, `, \ inside are literal. Same keys and printf placeholders as
# core.ru.sh. Missing here — the Russian text is shown (fallback en → ru → key).
M_en_LANG_NAME='English'
M_en_LANG_BAD_ENV='[!] KASKAD_LANG=%s ignored: use ru or en.'
M_en_LANG_MENU_NOW='Now: %s.'
M_en_LANG_MENU_NOTE='One language for the server: SSH menu, kaskadctl, web panel, setup wizard and bot.'
M_en_LANG_CHANGED='Language changed: %s.'
M_en_C_ENTER='Press Enter to continue'
M_en_C_CHOICE='Choice: '
M_en_C_CHOICE_DEF='Choice [%s]: '
M_en_C_BACK='Back'
M_en_C_CANCEL='Cancel'
M_en_C_NO_ITEM='No such item: enter a number from the list, 0 — back.'
M_en_C_NO_ITEM_RANGE='No such item: enter a number from 1 to %s%s (Enter — %s).'
M_en_C_ZERO_BACK=', 0 — back'
M_en_C_ZERO_OTHER=', 0 — %s'
M_en_C_NUM_ONE='number 1'
M_en_C_NUM_RANGE='a number from 1 to %s'
M_en_C_NO_ITEM_NUM='No such item: %s, 0 — back.'
M_en_C_PG_MORE='Enter — more, 0 — back to the actions: '
M_en_C_CORE_RC='the Kaskad core exited with code %s'
M_en_W_INB='protocol|protocols'
M_en_W_DEV='device|devices'

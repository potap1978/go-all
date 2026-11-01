echo "  · sitetpl: меню 19 «Домен и сайт», вторая часть — сайт-маскировка, каталог шаблонов, шаблон, адрес панели, проверка"
# Подключается из раздела 49H tools/k3_install_test.sh: помощники раздела 40 (sitef, site_on, site_off, tpl_page, MASK,
# FULL, site_st, CERT_LE, DNS_OK) и 49H (i18n_code, en, en_clean). Свои функции зовутся напрямую, без k3_site_menu
# (меню и шапка — у другого исполнителя). Данные подделок ядра для английских прогонов — латиницей.
use_root i18n-sitetpl; fake_ctl; engine_nft
i18n_code "меню 19, вторая часть (сайт, шаблоны, адрес панели, проверка)" \
    'k3_site_(cols|txt|tpl_fail|pick|card|tpl_word|refused|enable|disable|template|panel|check|panel_line)'

# каталог латиницей: категории, business — 2 страницы, поиск «cafe», landing — живость шаблонов
ST_CATS='[{"id": "business", "title": "Business and corporate", "icon": "briefcase", "count": 1174},
  {"id": "landing", "title": "Landing pages", "icon": "rocket", "count": 6654}]'
st_tpl_files() {
    sitef templates.json "{\"ok\": true, \"categories\": $ST_CATS, \"catalog_total\": 33467, \"items\": [], \"page\": 1, \"pages\": 1, \"total\": 33467}"
    sitef templates_category=business_page_1.json "$(tpl_page business 1 2 25 32 | jq -c '.items[].description = "Corporate site with a slider"')"
    sitef templates_category=business_page_2.json "$(tpl_page business 2 2 7 32 | jq -c '.items[].description = "Corporate site with a slider"')"
    sitef templates_category=business_query=cafe_page_1.json '{"ok": true, "items": [{"id": "gh_cafe", "name": "Cafe Daisy", "category": "business",
      "source": "github", "license": "", "preview": "https://example.org/cafe/", "description": "Cafe site with a menu", "repo": "https://github.com/fx/cafe",
      "author": "fx"}], "page": 1, "pages": 1, "per_page": 25, "total": 1, "category": "business", "query": "cafe"}'
    sitef templates_category=landing_page_1.json "$(tpl_page landing 1 1 3 3 | jq -c '.items[].description = "Landing page"
      | .items[0].health = {"state": "gone"} | .items[1].health = {"state": "no_preview"}')"
}
st_enable() { K3_SITE_NAME=vpn.example.com; K3_SITE_CERT=letsencrypt; K3_SITE_STAGING=false; K3_SITE_ROOT=/var/www/kaskad-site; scr k3_site_enable; }
st_disable() { K3_SITE_NAME=vpn.example.com; K3_SITE_ROOT=/var/www/kaskad-site; scr k3_site_disable; }
st_template() { K3_SITE_TEMPLATE="Good Business"; scr k3_site_template; }
st_panel() { scr k3_site_panel; }
st_check() { scr k3_site_check; }
st_pline() { k3_site_panel_line; }
ST_MODE_OK='{"ok": true, "site": '"$(site_on)"', "template": {"id": "gh_cafe", "name": "Cafe Daisy"}}'

# включить из каталога: категории → страница 1 → N → N (последняя) → P → поиск «cafe» → карточка → «да»
st_tpl_files; sitef status.json "$(site_off)"; sitef mode_site.json "$ST_MODE_OK"
o=$(printf '1\n1\nN\nN\nP\n/cafe\n1\n\n\n' | K3_TERM_COLS=80 en st_enable 2>&1); s=$(printf '%s' "$o" | strip); w=$(printf '%s' "$s" | flat)
en_clean "6 → 1: каталог, страницы, поиск, карточка, итог" "$o"
printf '%s' "$s" | grep -q '^ 1) Business and corporate (1,174)' && printf '%s' "$w" | grep -q 'Templates: 33467, all under free licenses\.' \
  && printf '%s' "$w" | grep -q 'Business and corporate — 32 found, page 1 of 2' \
  && printf '%s' "$w" | grep -q 'Number — pick · N — next · / — search · 0 — categories' \
  && printf '%s' "$w" | grep -q 'Number — pick · P — previous · / — search · 0 — categories' \
  && printf '%s' "$w" | grep -q 'This is the last page\.' \
  && printf '%s' "$w" | grep -q 'Business and corporate · search “cafe” — 1 found, page 1 of 1' \
  && printf '%s' "$w" | grep -q 'Cafe Daisy — Business and corporate' && printf '%s' "$w" | grep -q 'License: not specified; authors: fx' \
  && printf '%s' "$w" | grep -q 'Preview — open it in a browser' && printf '%s' "$w" | grep -q 'The site will go live with this template; you can change it any time: 19 → 7\.' \
  && printf '%s' "$w" | grep -q '1) Yes, this template 2) No, back to the list' \
  && printf '%s' "$w" | grep -q '✓ Masking site is on: https://vpn.example.com/ — template “Cafe Daisy”\.' \
  && ctl_log | grep -qx 'site mode site --template=gh_cafe --json' \
  && ok "EN каталог: «(1,174)», «32 found, page 1 of 2», N/P/поиск подсказкой, «last page», карточка, «Masking site is on»" \
  || bad "EN каталог" "$s"
r=$(printf '%s' "$s" | fit); [ "${r##* }" = ok ] && [ "$(printf '%s' "$s" | maxw)" -le 80 ] \
  && ok "EN каталог: каждый экран 80×24, строки до 80 столбцов ($r)" || bad "EN каталог: 80×24" "$r $(printf '%s' "$s" | maxw)"
# живость шаблонов: недоступен / без образца
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n2\n1\n2\n2\n0\n\n' | K3_TERM_COLS=80 en st_enable 2>&1); s=$(printf '%s' "$o" | strip); w=$(printf '%s' "$s" | flat)
en_clean "каталог: живость шаблонов" "$o"
printf '%s' "$s" | grep -q '^ 1) Landing Template Num… · unavailable' && printf '%s' "$s" | grep -q '^ 2) Landing Template Numb… · no preview' \
  && printf '%s' "$w" | grep -q 'Dimmed — removed by the author, can.t be picked\.' \
  && printf '%s' "$w" | grep -q 'Template “Landing Template Number 1 With A Very Long Name” was removed by its author — pick another one\.' \
  && printf '%s' "$w" | grep -q 'The author took the preview down; the template itself still installs\.' && ! ctl_log | grep -q '^site mode' \
  && ok "EN живость: «· unavailable», «· no preview», удалённый не выбрать, без образца — без превью" || bad "EN живость шаблонов" "$s"
# русский экран тот же: подсказка навигации и разделитель тысяч
o=$(printf '1\n1\n0\n\n' | K3_TERM_COLS=80 lib st_enable 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$o" | grep -q '^ 1) Business and corporate (1 174)' && printf '%s' "$w" | grep -q 'Номер — выбрать · N — далее · / — поиск · 0 — категории' \
  && printf '%s' "$w" | grep -q 'Шаблонов: 33467, все со свободными лицензиями' && printf '%s' "$w" | grep -q 'найдено 32, страница 1 из 2' \
  && ok "RU каталог — прежний: «(1 174)», «Номер — выбрать · N — далее…», «найдено 32, страница 1 из 2»" || bad "RU каталог" "$o"
o=$(printf '1\n1\nт\nз\n0\n\n' | K3_TERM_COLS=80 lib st_enable 2>&1 | strip)
printf '%s' "$o" | grep -q 'страница 2 из 2' && [ "$(printf '%s' "$o" | grep -c 'страница 1 из 2')" -ge 2 ] \
  && ok "т/з — синонимы N/P в русской раскладке (i18n-keep)" || bad "т/з" "$o"

# отказы ядра — причина, текст ядра, что сделать
st_refusal() {   # <reason> <EN-причина> <EN-совет> [template_reason]
    sitef mode_site.json "{\"ok\": false, \"reason\": \"$1\", \"text\": \"Core text for $1.\", \"error\": \"x\", \"code\": 3$([ -n "${4:-}" ] && echo ", \"template_reason\": \"$4\"")}"
    sitef mode_site.rc 3
    o=$(printf '\n\n' | K3_TERM_COLS=80 en st_enable 2>&1); w=$(printf '%s' "$o" | strip | flat)
    printf '%s' "$w" | grep -qF "✗ Site not turned on: $2." && printf '%s' "$w" | grep -qF "Core text for $1." && printf '%s' "$w" | grep -qF "$3"
}
sitef status.json "$(site_off)"
for c in "no_domain|no domain set|Set a domain: 19." "no_cert|no production Let’s Encrypt certificate|Issue one: 19 → 2" \
         "port80|port 80/tcp in use|“certificate without site” mode" "port443|port 443/tcp in use|work as before" \
         "neighbour|the server already hosts another program’s site (nginx on 443)|work as before" \
         "apt|couldn’t install nginx or git|try again in a few minutes" "nginx_test|nginx rejected the settings|The previous nginx settings are restored" \
         "template|template not installed — no GitHub key for the template catalog|Set the GitHub key: main menu → 8 → 6|nokey" \
         "template|template not installed — no connection to GitHub|Check the server’s network|net" \
         "template|template not installed — it has no ready index.html page|Pick another template (19 → 6 → 1)|no_index" \
         "template|template not installed — it didn’t download|Nothing changed: nginx and renewal are untouched.|download_failed"; do
    IFS='|' read -r a b d e <<< "$c"
    st_refusal "$a" "$b" "$d" "$e" && en_clean "отказ $a${e:+/$e}" "$o" || bad "EN отказ $a${e:+/$e}" "$o"
done
sitef mode_site.json '{"ok": true, "fallback": true, "site": {"site": {"panel_url_masked": null}}}'; sitef mode_site.rc 0
o=$(printf '2\n\n' | K3_TERM_COLS=80 en st_enable 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "6 → 2: заглушка" "$o"
printf '%s' "$w" | grep -q 'template “placeholder page”\.' && printf '%s' "$w" | grep -q 'a placeholder page is installed\. You can change the template: 19 → 7\.' \
  && printf '%s' "$w" | grep -q 'Web panel on the domain: being set up — full address: 19 → 8\.' && printf '%s' "$w" | grep -q '0) Cancel' \
  && ok "EN включить: заглушка, «placeholder page», «being set up», пункты Enter/1/2/0" || bad "EN заглушка" "$w"

# выключить: Enter — «Canceled.»; 1 — выключено
sitef status.json "$(site_on)"; sitef mode_domain.json "{\"ok\": true, \"site\": $(site_off)}"
o=$(printf '\n\n' | K3_TERM_COLS=80 en st_disable 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "6 (сайт есть): отмена" "$o"
printf '%s' "$w" | grep -q 'Turn off the masking site on vpn.example.com? What happens:' && printf '%s' "$w" | grep -q 'site files stay in /var/www/kaskad-site — turn on again: 19 → 6;' \
  && printf '%s' "$w" | grep -q 'Canceled\.' && ! ctl_log | grep -q '^site mode domain' \
  && ok "EN выключить: последствия, Enter — «Canceled.»" || bad "EN выключить: отмена" "$w"
o=$(printf '1\n\n' | K3_TERM_COLS=80 en st_disable 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "6 (сайт есть): выключить" "$o"
printf '%s' "$w" | grep -q '✓ Masking site is off: the domain and certificate are in place, site files kept\.' \
  && ok "EN выключить: «Masking site is off»" || bad "EN выключить" "$w"
# цена VLESS (§3.6): ядро отказало «vless_change» — текст цены и вопрос цифрами (Enter — отмена), «1» — то же с --yes
ST_PRICE_OFF='{"ok": false, "error": "The site goes off, VLESS goes to Reality as dl.google.com. RISKS-SHORT. Give VLESS links again (1 device: Phone).", "code": 3, "data": {"need": "vless_change", "kind": "site", "text": "The site goes off, VLESS goes to Reality as dl.google.com. RISKS-SHORT. Give VLESS links again (1 device: Phone).", "site": "dl.google.com", "need_pick": false}}'
sitef mode_domain.once.json "$ST_PRICE_OFF"; printf '3\n' > "$FAKE_DIR/site/mode_domain.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n\n' | K3_TERM_COLS=80 en st_disable 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "6 (сайт есть): цена VLESS, отмена" "$o"
printf '%s' "$w" | grep -q 'RISKS-SHORT. Give VLESS links again (1 device: Phone)\.' \
  && printf '%s' "$w" | grep -q '1) Switch the site off and give the VLESS keys out again' && printf '%s' "$w" | grep -q 'Canceled\.' \
  && [ "$(ctl_log | grep -c '^site mode domain')" = 1 ] && ! ctl_log | grep -q -- '--yes' \
  && ok "EN выключить при устройствах VLESS: цена с рисками, Enter — отмена, --yes не передан" || bad "EN выключить: цена, отмена" "$(ctl_log | tr '\n' ';') | $w"
sitef mode_domain.once.json "$ST_PRICE_OFF"; printf '3\n' > "$FAKE_DIR/site/mode_domain.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n\n' | K3_TERM_COLS=80 en st_disable 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "6 (сайт есть): цена VLESS, да" "$o"
ctl_log | grep -q '^site mode domain --yes' && printf '%s' "$w" | grep -q '✓ Masking site is off' \
  && ok "EN выключить при устройствах VLESS: «1» — site mode domain --yes, итог" || bad "EN выключить: цена, да" "$(ctl_log | tr '\n' ';') | $w"
# сайт маскировки не сохранён (need_pick): меню подбирает его с узла (без своего сайта), называет и спрашивает цену
gwf vless.masks.json '{"ok": true, "masks": [{"sni": "vpn.example.com", "mask": "own", "check": {"ok": true}}, {"sni": "www.microsoft.com", "mask": "list", "check": {"ok": false}}, {"sni": "dl.google.com", "mask": "list", "check": {"ok": true}}]}'
sitef mode_domain.once.json "$(printf '%s' "$ST_PRICE_OFF" | jq -c '.data.need_pick = true | .data.site = null')"; printf '3\n' > "$FAKE_DIR/site/mode_domain.once.rc"
sitef mode_domain_vless-site_dl.google.com.once.json "$ST_PRICE_OFF"; printf '3\n' > "$FAKE_DIR/site/mode_domain_vless-site_dl.google.com.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n\n' | K3_TERM_COLS=80 en st_disable 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "6 (сайт есть): цена VLESS, подбор сайта" "$o"
printf '%s' "$w" | grep -q 'Picked site: dl.google.com\.' && gw_calls | grep -qx 'gw vless masks --check --json' \
  && ctl_log | grep -q '^site mode domain --vless-site dl.google.com --yes' \
  && ok "EN выключить, сайт маскировки не сохранён: меню подобрало сайт (не свой), назвало, --vless-site и --yes" || bad "EN выключить: подбор" "$(ctl_log | tr '\n' ';') | $w"

# сменить шаблон: случайный с резервной копией; отказ с текстом ядра; нет ключа GitHub
sitef template_set_random.json '{"ok": true, "id": "gh_dist", "name": "Dist Layout", "fallback": false, "backup": "/var/backups/kaskad/site/previous", "warnings": []}'
o=$(printf '\n\n' | K3_TERM_COLS=80 en st_template 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "7: случайный шаблон" "$o"
printf '%s' "$w" | grep -q 'On the site now: “Good Business”\.' && printf '%s' "$w" | grep -q 'Enter) Random template — right away 1) Choose from the catalog 0) Back' \
  && printf '%s' "$w" | grep -q '✓ Template “Dist Layout” is on the site\.' && printf '%s' "$w" | grep -q 'Previous site saved: /var/backups/kaskad/site/previous\.' \
  && ok "EN сменить шаблон: «On the site now», «Template … is on the site», резервная копия" || bad "EN сменить шаблон" "$w"
sitef template_set_random.json '{"ok": false, "text": "Template gh_x did not download.", "fallback": false}'; sitef template_set_random.rc 1
o=$(printf '\n\n' | K3_TERM_COLS=80 en st_template 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "7: отказ" "$o"
printf '%s' "$w" | grep -q '✗ Template not installed: Template gh_x did not download\. The previous site is untouched\.' \
  && ok "EN сменить шаблон: отказ — текст ядра и «The previous site is untouched.»" || bad "EN сменить шаблон: отказ" "$w"
sitef templates.json '{"ok": false, "error": "no key", "code": 1, "data": {"reason": "nokey"}}'; sitef templates.rc 1
o=$(printf '1\n\n' | K3_TERM_COLS=80 en st_template 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "7 → 1: нет ключа GitHub" "$o"
printf '%s' "$w" | grep -q "✗ Can’t download the template catalog: no GitHub key\." && printf '%s' "$w" | grep -q 'Set the key: main menu → 8 → 6' \
  && ok "EN нет ключа GitHub: «Can't download the template catalog»" || bad "EN nokey" "$w"

# адрес панели: показать, сменить, отказ, отмена
sitef status.json "$(site_on)"
sitef panel-url_show.json "{\"ok\": true, \"url\": \"$FULL\", \"masked\": \"$MASK\"}"
sitef panel-path_rotate.json '{"ok": true, "url": "https://vpn.example.com/app-NeWpAtH0••••QwEr/"}'
o=$(printf '1\n1\n\n2\n1\n\n2\n2\n\n0\n' | K3_TERM_COLS=80 en st_panel 2>&1); s=$(printf '%s' "$o" | strip); w=$(printf '%s' "$s" | flat)
en_clean "8: адрес панели" "$o"
printf '%s' "$w" | grep -qF "Web panel on the domain: $MASK" && printf '%s' "$w" | grep -q '1) Show in full 2) Change address 0) Back' \
  && printf '%s' "$w" | grep -q "The address is a secret: don’t forward or publish it" && printf '%s' "$s" | grep -qxF "$FULL" \
  && printf '%s' "$w" | grep -q 'the old one stops opening right away' && printf '%s' "$w" | grep -qF '✓ New panel address: https://vpn.example.com/app-NeWpAtH0••••QwEr/. In full: 19 → 8 → 1.' \
  && printf '%s' "$w" | grep -q 'Canceled\.' \
  && ok "EN адрес панели: маска, «Show in full», полный адрес, «New panel address», отмена — «Canceled.»" || bad "EN адрес панели" "$s"
sitef panel-path_rotate.json '{"ok": false, "reason": "nginx_test", "text": "Old path kept.", "error": "x", "code": 3}'; sitef panel-path_rotate.rc 3
o=$(printf '2\n1\n\n0\n' | K3_TERM_COLS=80 en st_panel 2>&1); w=$(printf '%s' "$o" | strip | flat)
en_clean "8 → 2: отказ" "$o"
printf '%s' "$w" | grep -q '✗ Address not changed: nginx rejected the settings\.' && ok "EN адрес панели: отказ — «Address not changed»" || bad "EN адрес: отказ" "$w"

# проверка сайта: каждый вердикт
st_verdict() {   # <JSON> <ожидаемая фраза>
    sitef check.json "$1"; sitef check.rc 1
    o=$(printf '\n' | K3_TERM_COLS=80 en st_check 2>&1); w=$(printf '%s' "$o" | strip | flat)
    en_clean "9: $(printf '%s' "$1" | jq -r .verdict)" "$o"
    printf '%s' "$w" | grep -qF "$2" || bad "EN проверка $(printf '%s' "$1" | jq -r .verdict)" "$w"
}
st_verdict '{"ok": true, "verdict": "ok"}' '✓ The site is fine: the home page opens'
st_verdict '{"ok": false, "verdict": "dns_mismatch"}' "✗ The domain’s DNS doesn’t point to this server — the site won’t open for everyone. Check the A record: 19 → 1."
st_verdict '{"ok": false, "verdict": "no_cert"}' "✗ No valid Let’s Encrypt certificate — browsers won’t open the site. Issue one: 19 → 2."
st_verdict '{"ok": false, "verdict": "http_error", "http": {"root": 502, "redirect": 200}}' "(home page — code 502, http — code 200)"
st_verdict '{"ok": false, "verdict": "no_site"}' 'Masking site is off — nothing to check. Turn on: 19 → 6.'
st_verdict '{"ok": false, "verdict": "cert_expiring", "days_left": 9}' '! The site responds, but the certificate expires soon (9 days left). Renew: 19 → 2.'
st_verdict '{"ok": false, "verdict": "cert_expiring", "days_left": 1}' '(1 day left)'
st_verdict '{"ok": false, "verdict": "panel_unreachable", "why": "nginx cannot open the panel socket", "fix": "Update Kaskad."}' 'Cause: nginx cannot open the panel socket. Update Kaskad.'
o=$(printf '\n' | K3_TERM_COLS=80 lib st_check 2>&1 | strip | flat)
printf '%s' "$o" | grep -q '✗ Сайт открывается, а панель по секретному адресу не отвечает\. Проверьте веб-панель: 18\. Причина: nginx cannot open the panel socket\. Update Kaskad\.' \
  && ok "RU проверка — прежняя фраза и «Причина: …»" || bad "RU проверка" "$o"
sitef check.json '{"ok": false, "verdict": "cert_expiring", "days_left": 9}'
o=$(printf '\n' | K3_TERM_COLS=80 lib st_check 2>&1 | strip | flat)
printf '%s' "$o" | grep -qF '! Сайт отвечает, но сертификат скоро истекает (осталось 9 дн.). Продлить: 19 → 2.' \
  && ok "RU cert_expiring — «(осталось 9 дн.)» как раньше" || bad "RU cert_expiring" "$o"

# меню 18: строка о сертификате панели на домене
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" false false true)"
o=$(en st_pline 2>&1); r=$(lib st_pline 2>&1)
[ "$o" = "Let’s Encrypt certificate for vpn.example.com, until Dec 30, 2026" ] && [ "$r" = "сертификат Let's Encrypt для vpn.example.com, до 30.12.2026" ] \
  && ok "строка сертификата панели (меню 18): EN и RU" || bad "строка сертификата панели" "$o | $r"
sitef status.json "$(site_st "$CERT_SELF" "$DNS_OK" false false true)"
lib st_pline >/dev/null 2>&1 && bad "строка сертификата: самоподписанный — строки быть не должно" || ok "строка сертификата: самоподписанный — нет строки (1)"

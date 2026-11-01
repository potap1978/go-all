/* Kaskad 3 — страница человека (/p/<ссылка>): английские тексты (3.3.2). Те же ключи, что в user-i18n-ru.js;
   сервер подключает этот файл вместо русского, когда язык посетителя — английский (userpage.render_shell).
   Термины — docs/k3/GLOSSARY-en.md. Подстановки — {имя}. */
window.KASKAD_USER_I18N = {
  title: 'Connection settings',
  for_name: 'for',
  lang: 'Language',
  lang_ru: 'Русский',
  lang_en: 'English',
  lead: 'Here are the connection settings for your devices: links for apps, files, QR codes and short instructions.',
  secret: 'These settings are secret: don’t show this page or forward the link to anyone.',
  inapp: 'File won’t download or nothing gets copied? Open this link in a regular browser (Safari, Chrome), not in a messenger’s built-in window.',
  reveal: 'Show settings',
  hide: 'Hide settings',
  loading: 'Preparing settings…',
  busy: 'The server is busy, trying again…',
  retry: 'Retry',
  reload: 'Reload page',

  paused_title: 'Access paused',
  paused_text: 'The administrator has turned off your access for now. Contact the person who sent you the link. No need to remove the app from your device.',
  gone: 'This link no longer works. Ask the administrator for a new one.',
  stale: 'This page is out of date. Reload it and press the button again.',
  failed: 'Couldn’t get the settings. Try again a little later.',
  old_browser: 'This browser is too old for this page. Open the link in another one.',

  gateway_off: 'The administrator has turned the server off for now: you can’t connect until it’s back on.',
  no_devices: 'No devices have been added for you yet. Contact the administrator.',
  device_off: 'turned off by the administrator',
  no_inbounds: 'No connection method is turned on for this device yet. Contact the administrator.',

  inbound_error: 'Couldn’t prepare: {error}',


  qr_alt: 'QR code',

  platforms: {
    ios: 'iPhone / iPad', android: 'Android', androidtv: 'Android TV', windows: 'Windows', macos: 'macOS',
    linux: 'Linux', keenetic: 'Keenetic', mikrotik: 'MikroTik', openwrt: 'OpenWrt', tplink: 'TP-Link',
    huawei: 'Huawei', xiaomi: 'Xiaomi', asus: 'ASUS', other: 'Other'
  },

  theme_light: 'Light theme',
  theme_dark: 'Dark theme',

  // мелочи T-G1b
  protocols: 'Protocols',
  gone_title: 'This link no longer works',
  gone_text: 'It was replaced or turned off — or it wasn’t copied in full. Ask the person who sent it for a new link.',

  // 3.3.2 — the new look of the page (give.js)
  router_of: 'Router {b}',
  devices: 'Devices',
  personal: 'This link is personal — don’t share it.',
  brand: 'goKaskad PRO',
};

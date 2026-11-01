"""Network helpers of Kaskad 3 (wave 3): a SOCKS5 client and a small HTTP(S) client — stdlib only.

``kaskad.net.socks`` — ``parse_proxy`` (BOT_PROXY of /etc/kaskad/config), ``socks5_connect`` (RFC 1928, no
authentication, the destination name goes to the proxy as with ``socks5h``), connection classes for
``http.client`` that go directly, through SOCKS5 or through an HTTP proxy (CONNECT), ``request`` and
``multipart`` (uploads of documents and photos to the Telegram Bot API).
"""

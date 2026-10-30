#!/usr/bin/env python3
"""miniapp_proxy.py — мини-апп в браузере проверяющего без секретного пути панели в адресной строке.

    python3 miniapp_proxy.py [--port 18082]          # на сервере; с машины — ssh -L 18082:127.0.0.1:18082

http://127.0.0.1:18082/<путь> → https://<домен>/<секретный путь>/<путь> через настоящий nginx и настоящий сертификат
(соединение на 127.0.0.1:443, SNI и проверка — по домену). Панель видит запрос так же, как из Telegram: с
X-Forwarded-Host от своего nginx, то есть публичным, — вход только подписью initData (или кодом), туннельного доверия
нет. Прокси лишь прячет секретный путь: в адресах, Location и журналах браузера проверяющего его нет. Тела и заголовки
не меняются, кроме Host и Location. Слушает только петлю; запускается на время проверки (bot_live.sh) и убирается.
"""
import argparse
import http.client
import http.server
import json
import socket
import ssl
import sys

CONFIG = "/opt/goamnezia/config.json"
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade", "host",
       "content-length", "accept-encoding"}


def target():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    domain = str(cfg.get("domain") or "")
    path = str((cfg.get("panel") or {}).get("miniapp_path") or "").strip("/")
    if not domain or not path:
        sys.exit("panel on the domain is not configured")
    return domain, "/" + path


DOMAIN, PREFIX = target()


class LocalTLS(http.client.HTTPSConnection):
    """HTTPS к своему nginx на 127.0.0.1:443, но с SNI и проверкой сертификата по домену."""

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=DOMAIN)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = DOMAIN
        headers["Accept-Encoding"] = "identity"
        conn = LocalTLS(DOMAIN, 443, timeout=900, context=ssl.create_default_context())
        try:
            conn.request(self.command, PREFIX + self.path, body=body, headers=headers)
            r = conn.getresponse()
            data = r.read()
        except Exception as e:  # noqa: BLE001
            msg = ("proxy error: %s" % type(e).__name__).encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        finally:
            conn.close()
        self.send_response(r.status)
        for k, v in r.getheaders():
            kl = k.lower()
            if kl in HOP:
                continue
            if kl == "location":
                v = v.replace("https://" + DOMAIN + PREFIX, "").replace(PREFIX, "") or "/"
            if kl == "strict-transport-security":
                continue       # на петле по http — HSTS для localhost браузеру ни к чему
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _proxy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18082)
    a = ap.parse_args()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print("mini-app proxy on 127.0.0.1:%d → https://%s/<panel path>/ (via local nginx)" % (a.port, DOMAIN), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

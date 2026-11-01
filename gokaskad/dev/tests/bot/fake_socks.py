"""A fake SOCKS5 proxy (RFC 1928) on 127.0.0.1 in a thread, for the tests of kaskad.net.socks and the bot.

Records every CONNECT as (atyp, host, port); connects to ``routes[(host, port)]`` (or the address itself) and
relays bytes both ways. Misbehaviour on request: ``reply`` — a SOCKS error code instead of success,
``method`` — 0xFF (no acceptable method: wants a password), ``close_after_greeting``, ``silent`` (never answers).
"""

import socket
import struct
import threading
import time


def _recv(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise OSError("closed")
        buf += chunk
    return buf


class FakeSocks(object):
    def __init__(self, routes=None, reply=0, method=0, close_after_greeting=False, silent=False):
        self.routes = dict(routes or {})
        self.reply = reply
        self.method = method
        self.close_after_greeting = close_after_greeting
        self.silent = silent
        self.requests = []
        self.greetings = []
        self._stop = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.thread = threading.Thread(target=self._accept)
        self.thread.daemon = True

    @property
    def port(self):
        return self.sock.getsockname()[1]

    @property
    def url(self):
        return "socks5h://127.0.0.1:%d" % self.port

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop = True
        self.thread.join(2)
        self.sock.close()

    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            t = threading.Thread(target=self._serve, args=(conn,))
            t.daemon = True
            t.start()

    def _serve(self, conn):
        conn.settimeout(10)
        try:
            ver, n = _recv(conn, 2)
            methods = _recv(conn, n)
            self.greetings.append((ver, list(methods)))
            if self.silent:
                time.sleep(3)
                return
            if self.close_after_greeting:
                return
            conn.sendall(b"\x05" + bytes([self.method]))
            if self.method != 0:
                return
            ver, cmd, _, atyp = _recv(conn, 4)
            if atyp == 1:
                host = socket.inet_ntoa(_recv(conn, 4))
            elif atyp == 3:
                host = _recv(conn, _recv(conn, 1)[0]).decode("idna")
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, _recv(conn, 16))
            else:
                return
            port = struct.unpack(">H", _recv(conn, 2))[0]
            self.requests.append((atyp, host, port))
            if self.reply != 0:
                conn.sendall(b"\x05" + bytes([self.reply]) + b"\x00\x01" + b"\x00" * 6)
                return
            target = self.routes.get((host, port), (host, port))
            try:
                up = socket.create_connection(target, timeout=5)
            except OSError:
                conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                return
            # a domain-name BND.ADDR, to exercise every reply form of the client
            conn.sendall(b"\x05\x00\x00\x03" + bytes([9]) + b"localhost" + struct.pack(">H", up.getsockname()[1]))
            relay(conn, up)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def relay(a, b):
    """Copy bytes both ways until either side closes; both sockets are closed at the end."""
    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t = threading.Thread(target=pump, args=(b, a))
    t.daemon = True
    t.start()
    pump(a, b)
    t.join(5)
    a.close()
    b.close()

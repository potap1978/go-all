"""Только для проверок: egress_parse.py, которому сервер VLESS на петле 127.0.0.0/8 — не «этот узел».

3.3.6 круг 3 (SEC-336-awgaddr-2 Б-3): разборщик отвергает сервер ключа на петле (self_ep) — xray бота не идёт к службам
узла. Проверки снятия отпечатка (tofu) и живого xray поднимают свой TLS-сервер и сервер VLESS на 127.0.0.1 (127.0.0.2 —
«другой сервер»); им разборщик нужен с пропуском адресов петли IPv4, записанных IP. В установщике этого пропуска нет.

    python3 tests/parser_loopback.py <путь к egress_parse.py> <команда> …   — как «python3 egress_parse.py <команда> …»
    allow_loopback(module)                                                  — то же для модуля в процессе (mock)
"""

import importlib.util
import ipaddress
import sys

LOOPBACK = ipaddress.ip_network("127.0.0.0/8")


def _loop(host):
    try:
        return ipaddress.ip_address(str(host)) in LOOPBACK
    except ValueError:
        return False


def allow_loopback(mod):
    """mock.patch.object(mod, "_server_problem", …) для проверок в процессе: IP петли 127.x — годный сервер, остальное —
    как есть (localhost, ::1, числовые формы — по-прежнему отказ)."""
    orig = mod._server_problem
    return lambda host: "" if _loop(host) else orig(host)


def main(argv):
    spec = importlib.util.spec_from_file_location("egress_parse_loopback", argv[1])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._server_problem = allow_loopback(mod)
    sys.argv = [argv[1]] + argv[2:]
    mod.main()


if __name__ == "__main__":
    main(sys.argv)

#!/usr/bin/env python3
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

"""Focused unit regressions for Telegram bot helper behavior."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_DIR = ROOT / "phase-a" / "bot"
sys.path.insert(0, str(BOT_DIR))


# Bot helper imports only need these types at import time; avoid aiohttp locally.
class _RpcError(Exception):
    def __init__(self, message="", detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class _RpcTransportError(Exception):
    pass


sys.modules.setdefault(
    "rpc",
    types.SimpleNamespace(
        GovlessRpcClient=object,
        RpcError=_RpcError,
        RpcTransportError=_RpcTransportError,
        set_current_tg_id=lambda _uid: None,
        reset_current_tg_id=lambda _token: None,
    ),
)


class _DummyFilter:
    def __getattr__(self, _name):
        return self

    def __eq__(self, _other):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def startswith(self, *_args, **_kwargs):
        return self


class _DummyRouter:
    def __init__(self, *_args, **_kwargs):
        pass

    def message(self, *_args, **_kwargs):
        return lambda fn: fn

    def callback_query(self, *_args, **_kwargs):
        return lambda fn: fn


class _DummyButton:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _DummyMarkup:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


sys.modules.setdefault(
    "aiogram",
    types.SimpleNamespace(F=_DummyFilter(), Router=_DummyRouter),
)
sys.modules.setdefault(
    "aiogram.filters",
    types.SimpleNamespace(Command=lambda *_args, **_kwargs: object(), CommandObject=object),
)
sys.modules.setdefault(
    "aiogram.types",
    types.SimpleNamespace(
        CallbackQuery=object,
        InlineKeyboardButton=_DummyButton,
        InlineKeyboardMarkup=_DummyMarkup,
        Message=object,
    ),
)
sys.modules.setdefault("handlers.confirm", types.SimpleNamespace(ACTIONS={}, start_confirm=object()))

common = importlib.import_module("common")
clients = importlib.import_module("handlers.clients")


class BotRateLimitTests(unittest.TestCase):
    def test_callback_navigation_has_more_headroom_than_message_commands(self) -> None:
        limiter = common.RateLimiter()

        for _ in range(limiter.MESSAGE_PER_MINUTE):
            allowed, _retry_after = limiter.check(101, scope="callback")
            self.assertTrue(allowed)

        allowed, retry_after = limiter.check(101, scope="callback")

        self.assertTrue(allowed)
        self.assertIsNone(retry_after)

    def test_message_commands_still_enter_cooldown_after_ten_per_minute(self) -> None:
        limiter = common.RateLimiter()

        for _ in range(limiter.MESSAGE_PER_MINUTE):
            allowed, _retry_after = limiter.check(202, scope="message")
            self.assertTrue(allowed)

        allowed, retry_after = limiter.check(202, scope="message")

        self.assertFalse(allowed)
        self.assertEqual(retry_after, limiter.MESSAGE_COOLDOWN_SECONDS)


class BotResultFormattingTests(unittest.TestCase):
    def test_ok_result_is_not_rendered_as_python_dict(self) -> None:
        self.assertEqual(common.format_rpc_result_for_chat({"ok": True}), "")

    def test_new_subscription_url_result_is_human_readable(self) -> None:
        self.assertEqual(
            common.format_rpc_result_for_chat({"new_sub_url": "http://example/sub"}),
            "\nNew subscription URL:\n<code>http://example/sub</code>",
        )


class BotClientCardFormattingTests(unittest.TestCase):
    def test_expiry_timestamp_is_rendered_as_human_date(self) -> None:
        self.assertEqual(clients._fmt_expiry(1767225600), "2026-01-01 00:00 UTC")

    def test_expiry_zero_is_rendered_as_unlimited(self) -> None:
        self.assertEqual(clients._fmt_expiry(0), "—")

    def test_pro_client_card_prioritizes_subscription_before_direct_key(self) -> None:
        markup = clients._card_kb(
            {
                "uuid": "11111111-1111-1111-1111-111111111111",
                "enabled": True,
                "link": "vless://redacted",
                "sub_url": "https://example.test/sub/redacted",
            }
        )
        labels = [row[0].kwargs["text"] for row in markup.kwargs["inline_keyboard"] if row]

        self.assertLess(labels.index("🔗 Subscription URL"), labels.index("🔗 Send VLESS link"))


if __name__ == "__main__":
    unittest.main()

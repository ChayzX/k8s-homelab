#!/usr/bin/env python3
"""Startup must acquire witness authority before opening Discord."""

import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


def _decorator(*args, **kwargs):
    return lambda function: function


class _Group:
    def __init__(self, *args, **kwargs):
        pass

    command = _decorator


class _FakeBot:
    def __init__(self, *args, **kwargs):
        self.run = Mock()
        self.event = _decorator


def _import_main_without_optional_dependencies():
    discord = types.ModuleType("discord")
    discord.Intents = types.SimpleNamespace(none=lambda: object())
    discord.HTTPException = type("HTTPException", (Exception,), {})

    app_commands = types.ModuleType("discord.app_commands")
    app_commands.CommandTree = type("CommandTree", (), {})
    app_commands.Group = _Group
    app_commands.Choice = lambda **kwargs: types.SimpleNamespace(**kwargs)
    app_commands.AppCommandContext = lambda **kwargs: object()
    app_commands.AppInstallationType = lambda **kwargs: object()
    app_commands.command = _decorator
    app_commands.choices = _decorator
    app_commands.autocomplete = _decorator
    app_commands.allowed_contexts = _decorator
    app_commands.allowed_installs = _decorator

    commands = types.ModuleType("discord.ext.commands")
    commands.Bot = _FakeBot
    ext = types.ModuleType("discord.ext")
    ext.commands = commands
    discord.app_commands = app_commands
    discord.ext = ext

    kubernetes = types.ModuleType("kubernetes")
    kubernetes.client = types.ModuleType("kubernetes.client")
    kubernetes.client.rest = types.ModuleType("kubernetes.client.rest")
    kubernetes.client.rest.ApiException = type("ApiException", (Exception,), {})
    kubernetes.client.AppsV1Api = type("AppsV1Api", (), {})
    kubernetes.client.CoreV1Api = type("CoreV1Api", (), {})
    kubernetes.config = types.ModuleType("kubernetes.config")
    kubernetes.stream = types.ModuleType("kubernetes.stream")
    kubernetes.stream.stream = lambda *args, **kwargs: None

    health = types.SimpleNamespace(
        start=Mock(), mark_ready=Mock(), mark_not_ready=Mock(),
        start_background=Mock(return_value=Mock())
    )
    modules = {
        "discord": discord,
        "discord.app_commands": app_commands,
        "discord.ext": ext,
        "discord.ext.commands": commands,
        "kubernetes": kubernetes,
        "kubernetes.client": kubernetes.client,
        "kubernetes.client.rest": kubernetes.client.rest,
        "kubernetes.config": kubernetes.config,
        "kubernetes.stream": kubernetes.stream,
        "health": health,
    }
    with patch.dict(sys.modules, modules):
        sys.modules.pop("main", None)
        return importlib.import_module("main")


class MainStartupTests(unittest.TestCase):
    def test_discord_starts_only_after_standby_acquires_a_witness_lease(self):
        main = _import_main_without_optional_dependencies()
        main.DISCORD_BOT_TOKEN = "discord-token"
        main.ALLOWLIST = {1}
        main.bot.run.reset_mock()

        ownership = Mock()
        ownership.acquire.side_effect = [
            main.OwnershipError("no valid lease"),
            types.SimpleNamespace(site="oracle", epoch=4),
        ]
        with patch.object(main.Ownership, "from_env", return_value=ownership):
            with patch.object(main.time, "sleep") as sleep:
                main.main()

        sleep.assert_called_once_with(5)
        main.health.start_background.assert_called_once_with()
        main.bot.run.assert_called_once_with("discord-token")

    def test_standby_retries_until_the_witness_grants_a_lease(self):
        main = _import_main_without_optional_dependencies()
        lease = types.SimpleNamespace(site="oracle", epoch=4)
        ownership = Mock()
        ownership.acquire.side_effect = [
            main.OwnershipError("no valid lease"),
            lease,
        ]
        with patch.object(main.time, "sleep") as sleep:
            result = main.acquire_lease_until_available(ownership, retry_seconds=7)

        self.assertIs(result, lease)
        self.assertEqual(ownership.acquire.call_count, 2)
        sleep.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()

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
        "health": types.SimpleNamespace(start=Mock(), mark_ready=Mock()),
    }
    with patch.dict(sys.modules, modules):
        sys.modules.pop("main", None)
        return importlib.import_module("main")


class MainStartupTests(unittest.TestCase):
    def test_discord_is_not_started_when_witness_lease_acquisition_fails(self):
        main = _import_main_without_optional_dependencies()
        main.DISCORD_BOT_TOKEN = "discord-token"
        main.ALLOWLIST = {1}
        main.bot.run.reset_mock()

        ownership = Mock()
        ownership.acquire.side_effect = main.OwnershipError("no valid lease")
        with patch.object(main.Ownership, "from_env", return_value=ownership):
            with self.assertRaises(SystemExit) as exit_error:
                main.main()

        self.assertEqual(exit_error.exception.code, 1)
        main.bot.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""opsbot -- Discord bot for remote pod/deployment control + Minecraft RCON.

Slash commands only, gated by an explicit Discord user-ID allowlist
(DISCORD_USER_ID, see util.parse_user_allowlist), executing in-cluster via
the Kubernetes API (k8s_ops.py). This process makes only an OUTBOUND
connection to Discord's gateway -- zero new inbound network exposure.

Full design: `bd show k8s-homelab-bi6` (epic), `k8s-homelab-bi6.3` (this
command surface), `.4` (RCON bridge), `.5` (authorization + audit logging).
RBAC this code relies on: ../20-rbac.yaml.
"""
from __future__ import annotations

import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

import k8s_ops
import util

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
ALLOWLIST = util.parse_user_allowlist(os.environ.get("DISCORD_USER_ID"))


def _audit(interaction: discord.Interaction, authorized: bool, result: str = "") -> None:
    """One structured stdout line per command attempt: who, what, args,
    authorized y/n, and (once known) the result. No separate logging
    framework -- plain print(), matching this repo's existing scripts
    (minecraft_backup.py, minecraft_exporter.py). promtail ships stdout to
    Loki, so this line alone is the audit trail (bi6.5). Never let a
    logging-line failure take down the command it's describing.
    """
    command = interaction.command.qualified_name if interaction.command else "<unknown>"
    try:
        args = dict(interaction.namespace)
    except Exception:
        args = {}
    line = (
        f"[opsbot] user={interaction.user} user_id={interaction.user.id} "
        f"command={command} args={args} authorized={'y' if authorized else 'n'}"
    )
    if result:
        line += f" result={result}"
    print(line)


class OpsBotTree(app_commands.CommandTree):
    """Every slash command routes through here before its own handler runs.
    This is the ONE place the allowlist check lives -- a guard here covers
    every command, present and future, instead of relying on each handler
    to remember to call it (bi6.5's actual requirement: no command may run
    without this check)."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        authorized = util.is_authorized(interaction.user.id, ALLOWLIST)
        _audit(interaction, authorized)
        if not authorized:
            await interaction.response.send_message(
                "You are not authorized to use this bot.", ephemeral=True
            )
        return authorized

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = getattr(error, "original", error)
        command = interaction.command.qualified_name if interaction.command else "<unknown>"
        print(f"[opsbot] error command={command} error={original!r}")
        message = f"Error: {original}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


# No privileged Discord intents needed at all -- slash commands arrive as
# interaction events, which aren't gated by intents. Same least-privilege
# instinct as the Kubernetes RBAC in ../20-rbac.yaml.
intents = discord.Intents.none()
bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=OpsBotTree)

# Discord's API stores contexts/installs on the top-level command object --
# for a grouped command that's the Group itself, not each subcommand.
# Decorating pods_status/deploy_restart individually (an earlier attempt)
# was a silent no-op; confirmed live via GET .../commands showing
# contexts=None despite the subcommand decorators. Set on the Group's own
# constructor instead. Same guilds+dms, no private_channels/user-install
# reasoning as the standalone `mc` command below.
_dm_contexts = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=False)
_guild_only_install = app_commands.AppInstallationType(guild=True, user=False)

pods_group = app_commands.Group(
    name="pods", description="Pod status",
    allowed_contexts=_dm_contexts, allowed_installs=_guild_only_install,
)
deploy_group = app_commands.Group(
    name="deploy", description="Deployment control",
    allowed_contexts=_dm_contexts, allowed_installs=_guild_only_install,
)

_namespace_choices = [
    app_commands.Choice(name=ns, value=ns) for ns in sorted(util.ALLOWED_NAMESPACES)
]


async def _reply(interaction: discord.Interaction, text: str) -> None:
    """Send text as one or more followups, code-fenced and split to fit
    Discord's ~2000 char limit. Callers must defer() first."""
    for chunk in util.chunk_for_discord(text):
        await interaction.followup.send(chunk)


@pods_group.command(name="status", description="List pods in a namespace")
@app_commands.choices(namespace=_namespace_choices)
async def pods_status(interaction: discord.Interaction, namespace: str) -> None:
    await interaction.response.defer(thinking=True)
    # Defense in depth: RBAC already scopes opsbot-sa to exactly these three
    # namespaces, but a clean rejection here beats an opaque 403 from the API.
    if namespace not in util.ALLOWED_NAMESPACES:
        await _reply(interaction, f"Namespace {namespace!r} is not allowed.")
        _audit(interaction, True, result="rejected: namespace not allowed")
        return
    try:
        pods = k8s_ops.list_pods(namespace)
    except Exception as e:
        _audit(interaction, True, result=f"error: {e}")
        raise
    if not pods:
        text = f"No pods in {namespace}."
    else:
        text = "\n".join(
            f"{p['name']}  phase={p['phase']}  ready={p['ready']}  restarts={p['restarts']}"
            for p in pods
        )
    await _reply(interaction, text)
    _audit(interaction, True, result=f"{len(pods)} pods listed")


async def _deployment_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Live Deployment names for whichever namespace the user already
    picked -- so 'restart a pod' means tapping a real option, not
    remembering/spelling an exact name (the user's actual friction point).
    Autocomplete callbacks must never raise -- an empty list on any error
    just shows no suggestions, which is the correct fallback here."""
    namespace = interaction.namespace.namespace
    if not namespace or namespace not in util.ALLOWED_NAMESPACES:
        return []
    try:
        names = k8s_ops.list_deployment_names(namespace)
    except Exception:
        return []
    current_lower = current.lower()
    matches = [n for n in names if current_lower in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


@deploy_group.command(name="restart", description="Restart a Deployment (rollout restart)")
@app_commands.choices(namespace=_namespace_choices)
@app_commands.autocomplete(deployment=_deployment_autocomplete)
async def deploy_restart(interaction: discord.Interaction, namespace: str, deployment: str) -> None:
    await interaction.response.defer(thinking=True)
    if namespace not in util.ALLOWED_NAMESPACES:
        await _reply(interaction, f"Namespace {namespace!r} is not allowed.")
        _audit(interaction, True, result="rejected: namespace not allowed")
        return
    try:
        k8s_ops.restart_deployment(namespace, deployment)
    except k8s_ops.NotFoundError as e:
        await _reply(interaction, str(e))
        _audit(interaction, True, result=f"not found: {e}")
        return
    except Exception as e:
        _audit(interaction, True, result=f"error: {e}")
        raise
    await _reply(interaction, f"Restart triggered: {namespace}/{deployment}")
    _audit(interaction, True, result="restarted")


@app_commands.command(name="mc", description="Run a Minecraft RCON console command")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def mc(interaction: discord.Interaction, command: str) -> None:
    await interaction.response.defer(thinking=True)
    command = command.strip()
    if not command:
        await _reply(interaction, "Empty command.")
        _audit(interaction, True, result="rejected: empty command")
        return
    try:
        output = k8s_ops.exec_rcon(command)
    except k8s_ops.NotFoundError as e:
        await _reply(interaction, str(e))
        _audit(interaction, True, result=f"not found: {e}")
        return
    except Exception as e:
        _audit(interaction, True, result=f"error: {e}")
        raise
    await _reply(interaction, output)
    _audit(interaction, True, result="ok")


@bot.event
async def setup_hook() -> None:
    k8s_ops.init()
    bot.tree.add_command(pods_group)
    bot.tree.add_command(deploy_group)
    bot.tree.add_command(mc)
    await bot.tree.sync()
    print("[opsbot] slash commands synced")


@bot.event
async def on_ready() -> None:
    print(f"[opsbot] logged in as {bot.user} (id={bot.user.id if bot.user else '?'})")


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        print("[opsbot] FATAL: DISCORD_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    if not ALLOWLIST:
        print(
            "[opsbot] WARNING: DISCORD_USER_ID is not set or empty -- "
            "every command will be rejected",
            file=sys.stderr,
        )
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()

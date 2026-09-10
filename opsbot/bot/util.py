"""Pure helper functions for opsbot -- no Discord, no Kubernetes, no I/O.

Kept separate from main.py/k8s_ops.py specifically so this module can be
exercised with plain `assert`s and no live token/cluster (see the
`if __name__ == "__main__"` self-check at the bottom, run with
`python3 util.py`).
"""
from __future__ import annotations

import datetime
import os
import shlex

from _operations_contract import MUTATION_NAMESPACES

# Writable namespace allowlist. This mirrors the mutation-capable
# Role/RoleBinding set in ../20-rbac.yaml, and is now sourced from
# _operations_contract.py (a synced copy of Operations-ios-app's
# src/operations_api/contracts.py) so this set can't quietly drift from the
# web dashboard's restart policy -- see that file's header for why it's a
# copy rather than a package dependency. Observability is intentionally not
# here: the bot may report its health, but must not restart monitoring.
ALLOWED_NAMESPACES = MUTATION_NAMESPACES
# Read-only status coverage includes the monitoring stack itself. Keep this a
# separate set so adding a dashboard/status target can never accidentally add
# restart permission. Deliberately NOT sourced from the shared contract --
# Operations' STATUS_NAMESPACES is a broader, single-owner-only surface; see
# _operations_contract.py's header for why this stays a separate policy.
STATUS_NAMESPACES = ALLOWED_NAMESPACES | {"observability"}

# /bug (see gh_ops.py) -- which Discord-facing bot label maps to which
# GitHub repo. Each value is the (owner, repo) pair whose issue tracker owns
# that bot's bug reports; /bug files issues there via the GitHub REST API
# (gh_ops.py). The repos are the same ones surfaced on the GitHub Projects
# kanban board.
BOT_LABELS = {
    "music": "music bot (jmusicbot)",
    "pantry": "pantry-bot",
}
BOT_REPOS = {
    "music": ("ChayzX", "k8s-homelab"),
    "pantry": ("ChayzX", "pantry-bot"),
}
# If truthy (default), /bug is usable by ANY Discord user who can see the
# bot -- the point of the card is server members filing bugs. The reporter's
# Discord identity is still recorded in the GitHub issue and the audit log. Set to
# "false" to fall back to the DISCORD_USER_ID allowlist for everything.
REPORT_OPEN_ACCESS = os.environ.get("REPORT_OPEN_ACCESS", "true").strip().lower() == "true"

MINECRAFT_NAMESPACE = "minecraft"

# `/pods exec` is intentionally command-exec, never shell-exec. These are
# read-only diagnostics that are useful across arbitrary workloads without
# giving a Discord-facing bot a general-purpose shell primitive.
EXECUTABLES = frozenset({
    "cat", "df", "du", "env", "free", "head", "id", "ls", "printenv",
    "ps", "pwd", "uname", "uptime", "whoami",
})
EXEC_BLOCKED_TOKENS = frozenset({
    "/var/run/secrets", "/etc/shadow", "/etc/sudoers", "password", "passwd",
    "secret", "token", ".env",
})

DISCORD_MESSAGE_LIMIT = 2000
_CODE_FENCE = "```"
# Leave headroom for the fence itself (3 backticks + newline, twice) plus a
# small safety margin -- Discord counts the whole message including fences.
_CHUNK_BODY_LIMIT = DISCORD_MESSAGE_LIMIT - 2 * (len(_CODE_FENCE) + 1) - 10


def parse_user_allowlist(raw: str | None) -> frozenset[int]:
    """Parse DISCORD_USER_ID into a set of ints. Comma-separated, one value today."""
    if not raw:
        return frozenset()
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return frozenset(ids)


def is_authorized(user_id: int, allowlist: frozenset[int]) -> bool:
    return user_id in allowlist


def parse_exec_command(raw: str) -> list[str]:
    """Validate and tokenize a non-shell diagnostic command.

    The bot may target any pod, but it must not become an arbitrary remote
    shell. Shell metacharacters, command chaining, sensitive paths, and
    unknown executables are rejected before Kubernetes is called.
    """
    if not raw or len(raw) > 256:
        raise ValueError("command must be between 1 and 256 characters")
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"invalid command quoting: {exc}") from exc
    if not argv or argv[0] not in EXECUTABLES:
        raise ValueError(f"executable must be one of: {', '.join(sorted(EXECUTABLES))}")
    lowered = raw.lower()
    if any(token in lowered for token in (";", "&&", "||", "|", ">", "<", "`", "$(")):
        raise ValueError("shell operators are not allowed")
    if any(token in lowered for token in EXEC_BLOCKED_TOKENS):
        raise ValueError("sensitive paths or credentials are not readable")
    return argv


def rfc3339_now() -> str:
    """Current UTC time as RFC3339, e.g. 2026-08-11T00:00:00+00:00 --
    what `kubectl rollout restart` stamps into
    kubectl.kubernetes.io/restartedAt."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _utc(value: str) -> datetime.datetime:
    """Parse an RFC3339/ISO8601 timestamp, treating a bare (naive) one as UTC."""
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def format_pod_age(created_iso: str | None, now_iso: str | None = None) -> str:
    """Human-readable pod age in the compact `kubectl get pods` style
    (e.g. "2d3h", "3h12m", "5m4s"). Given as RFC3339; `created_iso` None or
    unparseable -> "?" so a rendering hiccup never crashes the status line.
    Clock skew (created in the future) clamps to "0s"."""
    if not created_iso:
        return "?"
    try:
        created = _utc(created_iso)
    except ValueError:
        return "?"
    now = _utc(now_iso) if now_iso else datetime.datetime.now(datetime.timezone.utc)
    delta = now - created
    if delta.total_seconds() < 0:
        delta = datetime.timedelta(0)
    seconds = int(delta.total_seconds())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def chunk_for_discord(text: str) -> list[str]:
    """Split text into <=2000-char Discord messages, each wrapped in a code
    block for readability. Always returns at least one chunk (a fenced
    "(no output)" placeholder for empty input)."""
    text = text.strip("\n") if text else ""
    if not text:
        text = "(no output)"
    chunks = []
    for i in range(0, len(text), _CHUNK_BODY_LIMIT):
        body = text[i : i + _CHUNK_BODY_LIMIT]
        chunks.append(f"{_CODE_FENCE}\n{body}\n{_CODE_FENCE}")
    return chunks


_REPORT_TITLE_MAX = 120


def report_title(bot_label: str, what: str) -> str:
    """Derive a GitHub issue title from the reporter's free text: first ~120 chars on
    one line, so Discord text with newlines becomes a clean one-line title.
    Never returns empty -- a blank description still produces a title."""
    summary = " ".join(what.split())[:_REPORT_TITLE_MAX].strip() or "no description"
    return f"{bot_label} bug: {summary}"


def bug_template(bot_label: str, reporter: str, reporter_id: int, what: str) -> str:
    """Fixed bug-report template for /bug (k8s-homelab-cq8). Every section is
    present even if the reporter's text is short; the markdown headers match
    what a hand-filed GitHub issue would contain, so the /bug-created issue
    reads the same as a manually created one. `reporter` is the Discord
    display name, `reporter_id` the numeric snowflake -- both go into the body
    (durable in GitHub) as well as the audit log."""
    return (
        "## Reported By\n"
        f"{reporter} (discord id {reporter_id}) via opsbot /bug\n"
        "\n"
        "## Bot\n"
        f"{bot_label}\n"
        "\n"
        "## What Happened\n"
        f"{what}\n"
        "\n"
        "## Steps to Reproduce\n"
        "Filed from Discord; reproduction steps TBD by the maintainer.\n"
        "\n"
        "## Acceptance Criteria\n"
        "Issue triaged; the reporter can verify the fix in Discord."
    )


def demo() -> None:
    """Smallest runnable self-check for this module's logic. No network, no cluster."""
    assert parse_user_allowlist("929216447723499562") == frozenset({929216447723499562})
    assert parse_user_allowlist(" 1, 2 ,3") == frozenset({1, 2, 3})
    assert parse_user_allowlist("") == frozenset()
    assert parse_user_allowlist(None) == frozenset()

    allowlist = parse_user_allowlist("929216447723499562")
    assert is_authorized(929216447723499562, allowlist) is True
    assert is_authorized(123, allowlist) is False

    assert parse_exec_command("ps aux")[0] == "ps"
    assert parse_exec_command("cat /etc/os-release") == ["cat", "/etc/os-release"]
    for unsafe in ("sh -c 'id'", "cat /var/run/secrets/token", "env | head"):
        try:
            parse_exec_command(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe command accepted: {unsafe}")

    assert "jmusicbot" in ALLOWED_NAMESPACES
    assert "keel" not in ALLOWED_NAMESPACES
    assert "observability" not in ALLOWED_NAMESPACES
    assert "observability" in STATUS_NAMESPACES

    now = rfc3339_now()
    # Must parse back as a valid RFC3339/ISO8601 timestamp with a UTC offset.
    parsed = datetime.datetime.fromisoformat(now)
    assert parsed.tzinfo is not None

    # format_pod_age: kubectl-style compact ages, clamping and error handling.
    assert format_pod_age("2026-08-11T00:00:00+00:00", "2026-08-13T00:00:00+00:00") == "2d0h"
    assert format_pod_age("2026-08-11T00:00:00+00:00", "2026-08-11T03:12:00+00:00") == "3h12m"
    assert format_pod_age("2026-08-11T00:00:00+00:00", "2026-08-11T00:05:04+00:00") == "5m4s"
    assert format_pod_age("2026-08-11T00:00:00+00:00", "2026-08-11T00:00:45+00:00") == "45s"
    # Naive (no offset) creation timestamps must be treated as UTC, like the
    # kubernetes client returns when creation_timestamp lacks an offset.
    assert format_pod_age("2026-08-11T00:00:00", "2026-08-11T02:00:00+00:00") == "2h0m"
    # A pod "created" in the future (clock skew) clamps to 0s instead of a
    # negative age, and garbage/None renders as "?" without raising.
    assert format_pod_age("2026-08-15T00:00:00+00:00", "2026-08-13T00:00:00+00:00") == "0s"
    assert format_pod_age(None) == "?"
    assert format_pod_age("not-a-timestamp") == "?"

    empty = chunk_for_discord("")
    assert len(empty) == 1 and "(no output)" in empty[0]

    short = chunk_for_discord("hello")
    assert len(short) == 1
    assert short[0] == "```\nhello\n```"
    assert len(short[0]) <= DISCORD_MESSAGE_LIMIT

    long_text = "x" * 5000
    long_chunks = chunk_for_discord(long_text)
    assert len(long_chunks) > 1
    assert all(len(c) <= DISCORD_MESSAGE_LIMIT for c in long_chunks)
    # Reassembling the fenced bodies should give back the original text.
    reassembled = "".join(c[len(_CODE_FENCE) + 1 : -(len(_CODE_FENCE) + 1)] for c in long_chunks)
    assert reassembled == long_text

    # /bug board routing: each bot label must resolve to a (owner, repo) pair.
    assert set(BOT_LABELS) == set(BOT_REPOS) == {"music", "pantry"}
    for label in BOT_LABELS.values():
        assert label
    assert BOT_REPOS["music"] == ("ChayzX", "k8s-homelab")
    assert BOT_REPOS["pantry"] == ("ChayzX", "pantry-bot")

    # report_title: one-line, bounded, never empty.
    assert report_title("pantry-bot", "snacks not showing") == "pantry-bot bug: snacks not showing"
    assert report_title("music bot (jmusicbot)", "  multi\nline\n  text  ") == (
        "music bot (jmusicbot) bug: multi line text"
    )
    assert report_title("pantry-bot", "   ") == "pantry-bot bug: no description"
    long = report_title("pantry-bot", "x" * 1000)
    assert len(long) <= _REPORT_TITLE_MAX + len("pantry-bot bug: ")

    # bug_template: reporter identity and every bug section present.
    t = bug_template("pantry-bot", "alice", 12345, "snacks not showing")
    assert "alice (discord id 12345) via opsbot /bug" in t
    assert "## Bot\npantry-bot\n" in t
    assert "## What Happened\nsnacks not showing\n" in t
    for section in ("## Reported By", "## Steps to Reproduce", "## Acceptance Criteria"):
        assert section in t
    assert len(bug_template("pantry-bot", "bob", 9, "")) > 0

    print("[opsbot] util.py self-check: OK")


if __name__ == "__main__":
    demo()

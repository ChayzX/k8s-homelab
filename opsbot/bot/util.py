"""Pure helper functions for opsbot -- no Discord, no Kubernetes, no I/O.

Kept separate from main.py/k8s_ops.py specifically so this module can be
exercised with plain `assert`s and no live token/cluster (see the
`if __name__ == "__main__"` self-check at the bottom, run with
`python3 util.py`).
"""
from __future__ import annotations

import datetime

# Namespace allowlist -- mirrors ../20-rbac.yaml's Role/RoleBinding set
# exactly (jmusicbot, pantry-bot, minecraft). Checked here too as defense in
# depth even though RBAC already enforces it server-side: a clean "not
# allowed" message beats an opaque 403 from the Kubernetes API.
ALLOWED_NAMESPACES = frozenset({"jmusicbot", "pantry-bot", "minecraft"})

MINECRAFT_NAMESPACE = "minecraft"

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


def rfc3339_now() -> str:
    """Current UTC time as RFC3339, e.g. 2026-08-11T00:00:00+00:00 --
    what `kubectl rollout restart` stamps into
    kubectl.kubernetes.io/restartedAt."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


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


def demo() -> None:
    """Smallest runnable self-check for this module's logic. No network, no cluster."""
    assert parse_user_allowlist("929216447723499562") == frozenset({929216447723499562})
    assert parse_user_allowlist(" 1, 2 ,3") == frozenset({1, 2, 3})
    assert parse_user_allowlist("") == frozenset()
    assert parse_user_allowlist(None) == frozenset()

    allowlist = parse_user_allowlist("929216447723499562")
    assert is_authorized(929216447723499562, allowlist) is True
    assert is_authorized(123, allowlist) is False

    assert "jmusicbot" in ALLOWED_NAMESPACES
    assert "keel" not in ALLOWED_NAMESPACES
    assert "observability" not in ALLOWED_NAMESPACES

    now = rfc3339_now()
    # Must parse back as a valid RFC3339/ISO8601 timestamp with a UTC offset.
    parsed = datetime.datetime.fromisoformat(now)
    assert parsed.tzinfo is not None

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

    print("[opsbot] util.py self-check: OK")


if __name__ == "__main__":
    demo()

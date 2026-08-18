"""Synced copy of Operations-ios-app's shared policy contract.

Source of truth: ChayzX/Operations-ios-app, src/operations_api/contracts.py
(POLICY_CONTRACT_VERSION "1", synced from commit 05095e7). This file is a
verbatim copy, not an independent redefinition -- see ChayzX/Operations-ios-app#26
and #29 for why opsbot doesn't `pip install` the parent package directly: it
would pull in FastAPI/uvicorn/pydantic-settings/pywebpush/pyjwt as transitive
dependencies for a Discord bot that only needs a few frozensets and two pure
functions, working against this image's deliberately minimal footprint (see
../Dockerfile's header comment). contracts.py itself has zero third-party
dependencies, which is what makes copying it safe and cheap.

When Operations-ios-app's contracts.py changes, re-sync this file by hand
(or via the automated cross-repo sync that #26 still leaves open) and bump
the "synced from commit" note above. util.py imports RESTART_ALLOWLIST /
MUTATION_NAMESPACES from here instead of redefining its own namespace set,
so the one policy surface that already matched between opsbot and Operations
(which namespaces are mutation-capable) can no longer silently drift apart.

STATUS_NAMESPACES here is intentionally NOT consumed by opsbot: Operations'
STATUS_NAMESPACES is deliberately broader (11 namespaces, including auth,
kube-system, ci-tunnel) because that surface sits behind Authentik and is
single-owner-only, whereas opsbot's Discord status surface
(bot/util.py's own STATUS_NAMESPACES) is scoped to a small allowlisted
Discord audience. Unifying the two would either widen what Discord can see
or narrow what the web dashboard shows -- a product decision, not a
technical cleanup, so it stays a deliberate divergence until the owner
decides otherwise.
"""
from __future__ import annotations

POLICY_CONTRACT_VERSION = "1"

RESTART_ALLOWLIST = frozenset(
    {
        ("jmusicbot", "jmusicbot"),
        ("minecraft", "minecraft"),
        ("pantry-bot", "pantry-bot"),
    }
)
MUTATION_NAMESPACES = frozenset(namespace for namespace, _ in RESTART_ALLOWLIST)


class HiddenNamespaceError(ValueError):
    """The requested namespace/deployment is not visible through this policy."""


def require_restart_target(namespace: str, deployment: str) -> tuple[str, str]:
    """Fail closed without revealing whether another deployment exists."""
    target = (namespace.strip().lower(), deployment.strip().lower())
    if target not in RESTART_ALLOWLIST:
        raise HiddenNamespaceError
    return target


__all__ = [
    "HiddenNamespaceError",
    "MUTATION_NAMESPACES",
    "POLICY_CONTRACT_VERSION",
    "RESTART_ALLOWLIST",
    "require_restart_target",
]

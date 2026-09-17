"""Validation for operator-configured local commands (argv-only)."""

from __future__ import annotations

import shlex
from pathlib import Path

_SHELL_META = frozenset(";&|<>`$(){}\n\r")


def parse_operator_command(raw: str, option: str) -> tuple[str, ...]:
    try:
        argv = tuple(shlex.split(raw, comments=False, posix=True))
    except ValueError as exc:
        raise ValueError(f"{option} must be valid argv") from exc
    if not argv:
        raise ValueError(f"{option} must not be empty")
    if not Path(argv[0]).is_absolute():
        raise ValueError(f"{option} executable must be an absolute path")
    if Path(argv[0]).name in {"sh", "bash", "dash", "zsh", "fish", "csh", "ksh"}:
        raise ValueError(f"{option} must not invoke a shell")
    if "-c" in argv or "--command" in argv:
        raise ValueError(f"{option} must not use command-evaluation flags")
    if any(any(char in token for char in _SHELL_META) for token in argv):
        raise ValueError(f"{option} contains shell metacharacters")
    return argv

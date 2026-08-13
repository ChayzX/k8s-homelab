"""Beads calls opsbot makes -- file bug beads from Discord (k8s-homelab-cq8).

The /report command runs `bd create` against a mounted beads checkout. Each
checkout is a directory whose only content is a project's `.beads/` dir
(hostPath-mounted by ../40-deployment.yaml); `--directory` is what routes the
new issue to the right board -- `bd create` discovers `.beads` by walking up
from there and auto-prefixes IDs from the database name (k8s-homelab-* vs
pantry-bot-*).

No kubeconfig, no kubectl, no git, no network: `bd create` is a local Dolt
write (verified: the git binary is never invoked, and a checkout with only
`.beads/` works). The `bd` binary itself ships in the image (see Dockerfile),
pinned to the same version the host uses so the on-disk Dolt schema matches
exactly.

Concurrency: the embedded Dolt backend permits exactly one writer and takes an
exclusive lock while a command runs. The host runs `bd` against these same
directories, so a pod-side create can occasionally find the lock held -- that
is a retryable condition, handled below. Any other failure is surfaced to the
user verbatim; a report must never silently vanish.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from util import BOT_LABELS, bug_template, report_title

BD_BIN = os.environ.get("BD_BIN", "bd")
# Per-attempt subprocess timeout. Originally 90s on the theory that lock
# contention fails fast with "exclusive lock" in stderr (the retryable path
# below) -- wrong in practice: a live collision with the host's `bd` (which
# runs against this same .beads/ dir constantly) can make `bd create` BLOCK
# on the lock instead of erroring, so the first live /report timed out and
# gave up with zero retries (TimeoutExpired used to raise immediately,
# skipping the retry loop entirely -- fixed below). 20s per attempt, across
# up to _MAX_ATTEMPTS, catches a genuine hang far faster while still giving
# the host's write a real window to finish.
_CREATE_TIMEOUT = 20
_LOCK_SUBSTR = "exclusive lock"
_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = (5, 10, 20)


class ReportError(Exception):
    """`bd create` failed; str() is safe to echo to the user in Discord."""


def _is_lock_contention(stderr: str) -> bool:
    return _LOCK_SUBSTR in stderr


def create_report(
    checkout: str,
    *,
    bot: str,
    reporter: str,
    reporter_id: int,
    what: str,
) -> str:
    """File a bug bead on the board at `checkout`. Returns the new issue ID
    (e.g. "pantry-bot-abc"). Raises ReportError on any failure; retries a few
    times with backoff when the embedded Dolt writer lock is held by the host.
    """
    if not os.path.isdir(checkout):
        raise ReportError(
            f"beads checkout {checkout!r} is not mounted in this pod -- "
            "see opsbot/40-deployment.yaml (hostPath volumes)"
        )
    label = BOT_LABELS[bot]
    cmd = [
        BD_BIN,
        "create",
        report_title(label, what),
        "-t",
        "bug",
        "-p",
        "2",
        "--silent",
        "--actor",
        reporter,
        "--metadata",
        json.dumps(
            {
                "source": "discord-report",
                "reporter_id": reporter_id,
                "reported_via": "opsbot",
            }
        ),
        "--description",
        bug_template(label, reporter, reporter_id, what),
        "--directory",
        checkout,
    ]

    last_err = ""
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            time.sleep(_BACKOFF_SECONDS[attempt - 1])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CREATE_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Treat a hang the same as fail-fast lock contention: retryable,
            # not an immediate give-up. A hang while the host holds the
            # write lock is the expected failure mode here, not the
            # exception -- see _CREATE_TIMEOUT's comment.
            last_err = f"`bd create` timed out after {_CREATE_TIMEOUT}s (attempt {attempt + 1}/{_MAX_ATTEMPTS})"
            continue
        except FileNotFoundError as e:
            raise ReportError(f"`{BD_BIN}` is not installed in this image (see Dockerfile)") from e

        stderr = proc.stderr.strip()
        if proc.returncode == 0:
            issue_id = proc.stdout.strip()
            if not issue_id:
                raise ReportError("`bd create` succeeded but returned no issue ID")
            return issue_id
        last_err = stderr or proc.stdout.strip()
        if not _is_lock_contention(stderr):
            break

    raise ReportError(f"`bd create` failed: {last_err or 'unknown error'}")

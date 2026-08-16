"""GitHub Issues calls opsbot makes -- file bug reports from Discord (k8s-homelab-cq8).

The /bug command creates a GitHub issue on the repo that owns the affected bot
via the GitHub REST API. No local database, no mounts: the pod calls
api.github.com directly with a fine-grained PAT (issues: write) scoped to the
two repos opsbot can file against (ChayzX/k8s-homelab, ChayzX/pantry-bot).

Networking: outbound HTTPS only, same trust model as the Discord gateway
connection -- zero new inbound exposure. The token is injected via the
opsbot-github Secret (../40-deployment.yaml); see ../SECRETS.md for how to
create it.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from util import BOT_LABELS, BOT_REPOS, bug_template, report_title

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
API_BASE = os.environ.get("GITHUB_API_BASE", "https://api.github.com")
_TIMEOUT = 20

# Labels applied to every /bug issue. All three must exist in both target
# repos (created once, see the migration labels setup): `bug` ships by
# default, `priority:P2` and `discord-report` were created alongside the
# migrated label set.
_BUG_LABELS = ["bug", "priority:P2", "discord-report"]


class ReportError(Exception):
    """GitHub issue creation failed; str() is safe to echo to the user in Discord."""


def create_report(
    bot: str,
    *,
    reporter: str,
    reporter_id: int,
    what: str,
) -> str:
    """File a bug issue on the GitHub repo owning `bot`. Returns the issue URL
    (e.g. https://github.com/ChayzX/pantry-bot/issues/42). Raises ReportError
    on any failure; the reporter's report must never silently vanish.
    """
    if not GITHUB_TOKEN:
        raise ReportError("GITHUB_TOKEN is not set -- see opsbot/SECRETS.md")
    if bot not in BOT_REPOS:
        raise ReportError(f"unknown bot: {bot!r} -- expected one of {sorted(BOT_REPOS)}")
    label = BOT_LABELS[bot]
    owner, repo = BOT_REPOS[bot]
    title = report_title(label, what)
    body = bug_template(label, reporter, reporter_id, what)

    payload = json.dumps(
        {"title": title, "body": body, "labels": _BUG_LABELS}
    ).encode("utf-8")
    url = f"{API_BASE}/repos/{owner}/{repo}/issues"
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise ReportError(f"GitHub API {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise ReportError(f"GitHub API request failed: {e}") from e

    number = data.get("number")
    if not number:
        raise ReportError("GitHub API returned no issue number")
    return f"https://github.com/{owner}/{repo}/issues/{number}"

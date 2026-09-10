# `opsbot` namespace

**Status: applied and live.** The bot's Python source (`opsbot/bot/*.py`)
landed and `40-deployment.yaml` is running (`opsbot` pod `1/1 Running`).
The original local issue tracker is retired — work now lives in GitHub Issues
on the kanban board (see `../AGENTS.md`).

Full design rationale and current open work are tracked in GitHub Issues.

Deploys **opsbot**: a standalone Discord bot giving remote, phone-friendly
control over specific homelab workloads — restart/status checks on
allowlisted Deployments, and Minecraft RCON console
access — entirely through Discord's own mobile app. The bot makes only an
OUTBOUND connection to Discord's gateway; there is no new inbound network
exposure of any kind.

Standalone rather than bolted onto jmusicbot: jmusicbot is a Java bot with its
own fragile OAuth/build pipeline, not a general plugin host — see the epic's
option-B rationale for the full comparison against a web dashboard (rejected:
more new attack surface) and a WireGuard VPN (rejected: more infra, still
needs client apps on the phone).

## `/pods exec` — diagnostics across the cluster (k8s-homelab-jhu)

Authorized operators can run `/pods exec` against a selected namespace, pod,
and container. The command is deliberately **not** a general shell: only the
read-only diagnostic executables listed in `bot/util.py` are accepted, shell
operators are rejected, credential paths are blocked, output is split into
Discord-sized replies, and Kubernetes execution is bounded by a 35-second
timeout. Every attempt is written to the Opsbot audit log.

This is the one operation that can target namespaces outside the normal
restart allowlist. `20-rbac.yaml` grants the service account a separate,
conspicuous `ClusterRole` for pod discovery and `pods/exec`; it does not grant
secrets, deletes, deployment mutation, or any other cluster-wide write access.
Apply that manifest and publish a new Opsbot image before using the command.

---

## Apply order

```bash
# 0. prerequisite — namespaces exist (this also creates jmusicbot and
#    pantry-bot's namespaces, which this doesn't touch, but opsbot's own
#    namespace comes from here too — namespaces are centralized, see
#    ../_bootstrap/00-namespaces.yaml)
kubectl apply -f ../_bootstrap/00-namespaces.yaml

# 1. identity
kubectl apply -f 10-serviceaccount.yaml

# 2. RBAC — Role + RoleBinding in each of jmusicbot, pantry-bot, minecraft
#    (minecraft's own namespace must already exist — see ../minecraft/)
kubectl apply -f 20-rbac.yaml

# 3. secrets (imperative, never in git) — see SECRETS.md
#    creates: opsbot-discord (DISCORD_BOT_TOKEN, DISCORD_USER_ID),
#    opsbot-github (GITHUB_TOKEN — fine-grained PAT, /bug),
#    ghcr-pull-secret (private GHCR image pull)

# 4. workload — will not come up until all three secrets exist AND
#    ghcr.io/chayzx/opsbot:latest has been published (see
#    .github/workflows/opsbot-deploy.yml)
kubectl apply -f 40-deployment.yaml
```

## `/bug` — bug issues from Discord (k8s-homelab-cq8)

`/bug <bot> <what happened>` files a bug issue on the GitHub repo that owns
the bot — `ChayzX/k8s-homelab` for `music`, `ChayzX/pantry-bot` for `pantry`
via the GitHub REST API (`bot/gh_ops.py`), over the pod's existing outbound
HTTPS path. The reporter's Discord identity is baked into the issue body;
open to any Discord user by default (`REPORT_OPEN_ACCESS`, flip to `false`
to restrict to `DISCORD_USER_ID`). Requires the `opsbot-github` Secret (a
fine-grained PAT, Issues Read+Write on those two repos) — see `SECRETS.md`.
No image rebuild or cluster manifests are needed to change the /bug target:
repo routing lives in `bot/util.py` (`BOT_REPOS`).

This replaces the original local-tracker-backed `/bug`. A filed issue lands on
GitHub immediately and is visible on the GitHub Projects board, and the pod is
no longer bound to a host-mounted issue database.

---

## RBAC summary

Namespaced `Role` + `RoleBinding` pairs — deliberately NOT a ClusterRole.
Full reasoning in `20-rbac.yaml`'s header comment.

| Namespace | Resource | Verbs |
|---|---|---|
| `jmusicbot`, `pantry-bot`, `minecraft` | `pods` | `get`, `list`, `watch` |
| `jmusicbot`, `pantry-bot`, `minecraft` | `deployments` (apps) | `get`, `list`, `watch`, `patch` |
| `observability` | `pods`, `deployments` (apps) | `get`, `list`, `watch` (status only) |

The `observability` namespace is status-only for restart commands:
`/pods status observability` is available for triage, while `/deploy restart
observability` is not offered and the service account has no deployment patch
permission. The separate `opsbot-pod-exec` ClusterRole is the only exception
to the old namespace boundary: it grants pod get/list/watch and `pods/exec`
get/create for the allowlisted, non-shell `/pods exec` diagnostics in any
namespace. It grants no `secrets`, delete/deletecollection, attach,
portforward, or deployment mutation access. (Contrast with `../ci-deploy/`'s
Role, a separate narrower credential used only by the GitHub Actions deploy
pipeline, not this bot.)

---

## What's still open (tracked in GitHub Issues, not blocking these manifests)

- GitHub issue for end-to-end test of `/bug` from the Discord mobile app (the
  app must be reinstalled/published for slash-command sync — see `main.py`
  `setup_hook`).

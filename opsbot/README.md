# `opsbot` namespace

**Status: applied and live.** The bot's Python source (`opsbot/bot/*.py`,
`bd show k8s-homelab-bi6.3`) landed and `40-deployment.yaml` is running
(`opsbot` pod `1/1 Running`). `bi6.1` (Discord application/bot token setup)
is still open in Beads as of this writing even though the Secret exists and
the pod is healthy — check `bd show k8s-homelab-bi6.1` before assuming that
task is fully closed out. See `ARCHITECTURE.md`'s `opsbot` section for the
current live-status summary.

Full design rationale, options compared, and open questions:
`bd show k8s-homelab-bi6` (epic). RBAC-specific reasoning:
`bd show k8s-homelab-bi6.2`.

Deploys **opsbot**: a standalone Discord bot giving remote, phone-friendly
control over specific homelab workloads — restart/status checks on
allowlisted Deployments, and (in a later task, `bi6.4`) Minecraft RCON console
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
#    ghcr-pull-secret (private GHCR image pull)

# 4. workload — will not come up until both secrets exist AND
#    ghcr.io/chayzx/opsbot:latest has been published (see
#    .github/workflows/opsbot-publish.yml, or build manually per SECRETS.md)
kubectl apply -f 40-deployment.yaml
```

## `/bug` — bug beads from Discord (k8s-homelab-cq8)

`/bug <bot> <what happened>` files a bug bead on the correct board by
running `bd create` inside the pod against hostPath-mounted **live** beads
databases (`/home/chase/k8s-homelab/.beads` → k8s-homelab board,
`/home/chase/Downloads/pantry-bot/.beads` → pantry-bot board). The reporter's
Discord identity is baked into the bead; open to any Discord user by default
(`REPORT_OPEN_ACCESS`, flip to `false` to restrict to `DISCORD_USER_ID`).
Requires a rebuilt image (the `bd` binary is baked in, pinned to the host's
version) and the two hostPath volumes in `40-deployment.yaml`. Rebuild via
`.github/workflows/opsbot-publish.yml` (push to `opsbot/bot/**`, or
`workflow_dispatch`) then `.github/workflows/opsbot-deploy.yml`
(`workflow_dispatch`, manual-click) — see `ARCHITECTURE.md`'s CI/CD note.

Because the pod writes the host's own Dolt databases, a filed bug appears in
the board immediately and reaches GitHub on the host's next `bd dolt push`
— no new credentials, no push-from-pod. Known limits: single-node only
(node == workstation), and pod/host `bd` writes serialize on the embedded
Dolt lock (`bd_ops.py` retries on lock contention, including on the host's
own `bd` writes racing the pod's — see its own comments).

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

## What's still open (tracked in Beads, not blocking these manifests)

- `bi6.1` — Discord application + bot token (open in Beads; Secret exists and pod is healthy, ticket not yet formally closed — see status note above)
- `bi6.7` — end-to-end test from the Discord mobile app
- `bi6.8` — README/ARCHITECTURE.md updates once this is live (this edit)

Closed: `bi6.3` (bot source), `bi6.4` (RCON bridge command), `bi6.5` (allowlist + audit logging).

# `opsbot` namespace

**Status: manifests only, not yet applied.** The Discord application/bot
token don't exist yet (`bd show k8s-homelab-bi6.1`) and the bot's Python
source doesn't exist yet (`bd show k8s-homelab-bi6.3`), so `40-deployment.yaml`
cannot run. This directory is the approved design turned into files, ready to
apply once both prerequisites land.

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

---

## Apply order

```bash
# 0. prerequisite — namespaces exist (this also creates jmusicbot, pantry-bot
#    and keel's namespaces, which this doesn't touch, but opsbot's own
#    namespace comes from here too — namespaces are centralized, see
#    ../_bootstrap/00-namespaces.yaml)
kubectl apply -f ../_bootstrap/00-namespaces.yaml

# 1. identity
kubectl apply -f 10-serviceaccount.yaml

# 2. RBAC — Role + RoleBinding in each of jmusicbot, pantry-bot, minecraft
#    (minecraft's own namespace must already exist — see ../minecraft/)
kubectl apply -f 20-rbac.yaml

# 3. secret (imperative, never in git) — see SECRETS.md
#    creates: opsbot-discord (DISCORD_BOT_TOKEN, DISCORD_USER_ID)

# 4. workload — will not come up until the opsbot-discord Secret exists AND
#    localhost/opsbot:dev has been built and side-loaded (see
#    40-deployment.yaml's header comment)
kubectl apply -f 40-deployment.yaml
```

---

## RBAC summary

Namespaced `Role` + `RoleBinding` pairs — deliberately NOT a ClusterRole,
unlike `../keel`. Full reasoning in `20-rbac.yaml`'s header comment.

| Namespace | Resource | Verbs |
|---|---|---|
| `jmusicbot`, `pantry-bot`, `minecraft` | `pods` | `get`, `list`, `watch` |
| `jmusicbot`, `pantry-bot`, `minecraft` | `deployments` (apps) | `get`, `list`, `watch`, `patch` |

Excluded on purpose: `keel` and `observability` namespaces (not in the
approved whitelist), any `secrets` verb, `delete`/`deletecollection`,
`exec`/`attach`/`portforward`, and anything cluster-scoped.

---

## What's still open (tracked in Beads, not blocking these manifests)

- `bi6.1` — Discord application + bot token
- `bi6.3` — bot source (`opsbot/bot/*.py`) — a separate task/agent, not this directory
- `bi6.4` — RCON bridge command (will need its own Secret-access decision — see `SECRETS.md`'s "Not in scope" section)
- `bi6.5` — allowlist + audit logging in bot code
- `bi6.7` — end-to-end test from the Discord mobile app
- `bi6.8` — README/ARCHITECTURE.md updates once this is live

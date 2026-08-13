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

## `/report` — bug beads from Discord (k8s-homelab-cq8)

`/report <bot> <what happened>` files a bug bead on the correct board by
running `bd create` inside the pod against hostPath-mounted **live** beads
databases (`/home/chase/k8s-homelab/.beads` → k8s-homelab board,
`/home/chase/Downloads/pantry-bot/.beads` → pantry-bot board). The reporter's
Discord identity is baked into the bead; open to any Discord user by default
(`REPORT_OPEN_ACCESS`, flip to `false` to restrict to `DISCORD_USER_ID`).
Requires a rebuilt+side-loaded image (the `bd` binary is baked in, pinned to
the host's version) and the two hostPath volumes in `40-deployment.yaml`:

```bash
docker build -t localhost/opsbot:dev opsbot/bot/
docker save localhost/opsbot:dev | sudo k3s ctr images import -
kubectl apply -f 40-deployment.yaml
```

Because the pod writes the host's own Dolt databases, a filed bug appears in
the board immediately and reaches GitHub on the host's next `bd dolt push`
— no new credentials, no push-from-pod, no Keel dependency. Known limits:
single-node only (node == workstation), and pod/host `bd` writes serialize on
the embedded Dolt lock (bd_ops.py retries).

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

- `bi6.1` — Discord application + bot token (open in Beads; Secret exists and pod is healthy, ticket not yet formally closed — see status note above)
- `bi6.7` — end-to-end test from the Discord mobile app
- `bi6.8` — README/ARCHITECTURE.md updates once this is live (this edit)

Closed: `bi6.3` (bot source), `bi6.4` (RCON bridge command), `bi6.5` (allowlist + audit logging).

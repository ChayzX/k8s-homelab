# Secrets — `keel` namespace

No secret values live in this repo. Create the Secret below imperatively,
**before** applying `30-deployment.yaml`.

---

## Why Keel needs its own credentials — this is the least-obvious failure mode in the whole migration

There are **two separate GHCR credentials in play**, used by two different
processes, and they are easy to conflate:

| Who | What it does | Credential | Lives in |
|---|---|---|---|
| **kubelet** | pulls `ghcr.io/chayzx/pantry-bot:latest` to actually run the container | `ghcr-pull-secret` (`imagePullSecrets` on the pod spec) | `pantry-bot` namespace |
| **Keel** | polls the GHCR **registry API** every 5 minutes asking "what digest does `:latest` point to right now?" | `keel-registry-creds` (this document) | `keel` namespace |

`ghcr.io/chayzx/pantry-bot` is private (verified: an anonymous manifest fetch
against it returns HTTP 403). Keel's poll is a plain registry API call and
gets the same 403 without its own auth — but **that failure is silent**. The
pod keeps running whatever it already has. Nothing crashes. Nothing shows
`ImagePullBackOff`. The CI → GHCR → auto-redeploy pipeline just quietly stops,
and the only symptom is "huh, that fix from three days ago still isn't live."

---

## Two ways Keel can get credentials — verified against the keel-hq/keel source

Read directly from `secrets/secrets.go` and `cmd/keel/main.go` in the
keel-hq/keel repo (not assumed from the docs, which are thin on this exact
point):

1. **Automatic discovery from the watched pod's own `imagePullSecrets`.** For
   each tracked image, Keel lists the pods matching the Deployment's selector
   in that Deployment's namespace, reads their `imagePullSecrets` field, and
   tries those Secrets first. This is why the ClusterRole in
   `20-clusterrole-clusterrolebinding.yaml` grants `get`/`list`/`watch` on pods
   AND secrets cluster-wide — without both, this path cannot work at all.

2. **A default credential set from the `DOCKER_REGISTRY_CFG` environment
   variable**, checked FIRST, before per-pod discovery. If set, it's used for
   every tracked image whose registry matches a key in it, regardless of that
   image's own `imagePullSecrets`.

This repo uses **mechanism 2, explicitly**, rather than relying on mechanism 1.
Reasoning: mechanism 1 depends on Keel's pod-selector lookup correctly matching
`pantry-bot`'s pods, in the right namespace, every single poll — a chain with
several silent-failure points and no direct way to unit-test it from outside
Keel's own logs. An explicit `DOCKER_REGISTRY_CFG` Secret is one fewer moving
part and easier to verify (see below). If you'd rather rely on auto-discovery
instead, you can — the RBAC already supports it and you can drop this Secret —
but do the verification steps at the bottom either way.

---

## Create `keel-registry-creds`

`DOCKER_REGISTRY_CFG` must be the **`.dockerconfigjson`-shaped** payload
(`{"auths": {"<registry>": {"username", "password", "auth"}}}`) — confirmed by
reading `DecodeDockerCfgJson` in `secrets/secrets.go`, which unmarshals it as
exactly that structure. It is NOT base64-wrapped at the env-var level; the
value is the raw JSON text.

```bash
GH_USER='chayzx'
GH_PAT='REPLACE_ME'   # needs read:packages scope — see below

DOCKER_CFG_JSON=$(jq -n \
  --arg u "$GH_USER" \
  --arg p "$GH_PAT" \
  --arg auth "$(printf '%s:%s' "$GH_USER" "$GH_PAT" | base64 -w0)" \
  '{auths: {"ghcr.io": {username: $u, password: $p, auth: $auth}}}')

kubectl -n keel create secret generic keel-registry-creds \
  --from-literal=DOCKER_REGISTRY_CFG="$DOCKER_CFG_JSON"
```

The PAT needs the **`read:packages`** scope. Same requirement, same gotcha, as
the kubelet's `ghcr-pull-secret` documented in `../pantry-bot/SECRETS.md` — a
PAT with only `repo` scope authenticates against GitHub fine and still gets a
403 from the registry itself.

You can reuse the exact same PAT used for `ghcr-pull-secret`, or mint a
separate one — GHCR does not distinguish "kubelet pull" from "Keel poll",
they're both just registry API auth. A separate PAT only matters if you want
independent revocation/audit trails for the two consumers.

Test the PAT directly against the registry before wiring it in:

```bash
T=$(curl -s -u "$GH_USER:$GH_PAT" \
  'https://ghcr.io/token?scope=repository:chayzx/pantry-bot:pull&service=ghcr.io' \
  | jq -r .token)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" \
  https://ghcr.io/v2/chayzx/pantry-bot/manifests/latest
# 200 = good. 401/403 = bad PAT or missing read:packages.
```

---

## Verify Keel is actually authenticating — do not trust silence

```bash
kubectl -n keel logs deploy/keel --tail=100 | grep -iE 'pantry|ghcr|unauthor|401|403|digest|registry'
```

Look for evidence Keel resolved a digest for `ghcr.io/chayzx/pantry-bot`, not
just that the pod is `Running` — `Running` proves nothing about whether polling
is authenticating correctly.

Then force a real end-to-end test, because a log line saying "digest resolved"
still isn't proof the *update* path works:

```bash
# push a trivial commit to pantry-bot's main branch, wait for CI to publish,
# then watch for a new pod:
kubectl -n pantry-bot get pods -w
```

A pod recreated within roughly 5-10 minutes of the CI publish (poll schedule is
`@every 5m`) confirms the whole chain: Keel polled → saw a new digest → had
credentials to compare it → patched the Deployment → `strategy: Recreate` tore
down and replaced the pod.

If nothing happens after ~15 minutes, check in this order:
1. `kubectl -n keel logs deploy/keel` for 401/403 → credentials.
2. `kubectl -n pantry-bot get deploy pantry-bot -o yaml | grep keel.sh` →
   confirm the three annotations from `../pantry-bot/40-deployment.yaml` are
   actually present (they can be lost if the Deployment was ever re-applied
   from a stripped-down manifest).
3. `kubectl -n keel get clusterrolebinding keel -o yaml` → confirm the
   ServiceAccount binding survived.

---

## Checklist before applying the Deployment

```bash
kubectl -n keel get secret keel-registry-creds
```

Must exist. Never commit it, never `kubectl get -o yaml` it into a paste — the
value contains a live GitHub PAT.

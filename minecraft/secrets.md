# Minecraft Secrets

Native Kubernetes `Secret`, created imperatively. **Never commit the real
values** — this repo's `.gitignore` blocks `*.secret`, `secret*.yaml`,
`secret*.yml`, and `*.env`, but the discipline that actually matters is: don't
type the real password into a file under version control in the first place.

Honest caveat (carried from the migration plan): this buys config-delivery
hygiene, not true secrets hygiene. `server.properties` on the PVC still holds
`rcon.password` and `management-server-secret` in plaintext — Paper has no
other way to consume them — so the k8s Secret's job is to keep the value out
of the Deployment manifest and out of shell history, not to encrypt it at
rest. `etcd`/k3s's sqlite datastore is unencrypted by default on this
single-node setup.

## Prerequisite

Read the real values only from the live file, never retype them:

```
/home/chase/minecraft/server.properties
```

Look at `rcon.password` and `management-server-secret`. Do **not** paste
those values into any file in `k8s-homelab/` — pass them on the command line
(below) or, better, via `--from-file` so they never appear in shell history
either.

## Create the Secret

Only `rcon.password` is consumed by anything in this manifest set today (the
container's `preStop` hook and `minecraft_backup.py`, both via
`/opt/minecraft/rcon.jar`). `management-server-secret` is included because
`server.properties` defines it, `management-server-enabled=false` currently so
nothing reads it — but it's captured now so the Secret object doesn't need a
second creation pass if that feature is turned on later.

**Option A — literals (values land in shell history; use Option B if that
matters to you):**

```bash
kubectl create secret generic minecraft-rcon \
  --namespace minecraft \
  --from-literal=rcon.password='REPLACE_WITH_VALUE_FROM_server.properties' \
  --from-literal=management-server-secret='REPLACE_WITH_VALUE_FROM_server.properties'
```

**Option B — from-file (recommended; nothing touches history or a shell
argument list):**

```bash
# Run interactively — do not put the values in a script file.
umask 077
read -r -s -p "rcon.password: " RCON_PW; echo
printf '%s' "$RCON_PW" > /tmp/rcon.password
read -r -s -p "management-server-secret: " MGMT_SECRET; echo
printf '%s' "$MGMT_SECRET" > /tmp/management-server-secret
unset RCON_PW MGMT_SECRET

kubectl create secret generic minecraft-rcon \
  --namespace minecraft \
  --from-file=rcon.password=/tmp/rcon.password \
  --from-file=management-server-secret=/tmp/management-server-secret

shred -u /tmp/rcon.password /tmp/management-server-secret
```

The namespace must exist first (`minecraft.yaml` creates it) — run:

```bash
kubectl apply -f minecraft.yaml -l homelab.chase/cutover-stage=pre
```

before either command above, or `kubectl create secret` will fail with
`namespaces "minecraft" not found`.

## Verify (without printing the value)

```bash
kubectl get secret minecraft-rcon -n minecraft -o jsonpath='{.data}' | jq 'keys'
# expect: ["management-server-secret","rcon.password"]
```

## Keeping it in sync

`rcon.password` in this Secret **must byte-for-byte match** `rcon.password` in
`server.properties` on the PVC. They are two independent values — nothing
enforces they agree. If you ever rotate the password:

1. Edit `rcon.password` in `server.properties` on the PVC (via `kubectl exec`
   or by editing the file directly under the PV's `local-path` directory).
2. `kubectl delete secret minecraft-rcon -n minecraft && kubectl create secret ...` (Secrets have no `kubectl edit`-friendly update path for individual keys; delete and recreate, or use `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`.)
3. `kubectl rollout restart deployment/minecraft -n minecraft` — env vars from
   a `secretKeyRef` are resolved at pod start and do **not** hot-reload.
4. Restart drops players; treat it like any other cutover-adjacent operation
   and warn whoever is online.

If they drift, `preStop`'s RCON auth fails silently-by-design (it falls
through to SIGTERM rather than blocking shutdown — see `minecraft.yaml`), and
`minecraft_backup.py`'s `kubectl exec ... save-all` starts failing loudly. A
failing backup job is the signal to check this first.

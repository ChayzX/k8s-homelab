# ci-tunnel — manual setup

Everything in this directory (`00-namespace.yaml`, `10-serviceaccount.yaml`,
`20-deployment.yaml`) is the connector *pod*. The tunnel itself, its public
hostname, and Cloudflare Access are dashboard-managed — same convention as
every other tunnel in this repo (see the retired `../pantry-bot/60-deployment-cloudflared.yaml.retired`'s
header). These steps can't be scripted from here: this session has no
Cloudflare API credentials, only what's checked into git.

## 1. Create the tunnel (Zero Trust dashboard → Networks → Tunnels)

New tunnel, name it something like `k8s-homelab-ci`. **Do not reuse** the
existing pantry-bot tunnel — see `00-namespace.yaml`'s header for why
(blast-radius isolation). Copy the generated token for step 4.

## 2. Public hostname → origin

Add a public hostname (e.g. `k8s-api.greeniespantry.uk`) on the new tunnel,
origin service `https://kubernetes.default.svc:443`.

**The gotcha**: `kubernetes.default.svc`'s TLS cert is signed by the
cluster's own internal CA, not a public one — cloudflared's default origin
TLS verification will reject it. In the hostname's *Additional application
settings → TLS*, either:
- set **No TLS Verify** (simplest, and the actual bearer-token auth still
  happens at the K8s API layer regardless — this doesn't weaken that), or
- supply the cluster CA explicitly (`kubectl config view --minify --raw`
  gives the base64 CA under `clusters[0].cluster.certificate-authority-data`)
  as the origin CA pool, if you want cert pinning too.

## 3. Cloudflare Access — Service Token (Zero Trust → Access → Service Auth)

Create a Service Token (e.g. `github-actions-ci`). Note the **Client ID** and
**Client Secret** — shown once. Then add an Access Application for the
`k8s-api.greeniespantry.uk` hostname with a policy allowing *only* that
service token (Action: Service Auth). This is what stands between the
public internet and your cluster's control plane — everything below assumes
this policy is correctly scoped to just the one token, not "Everyone".

## 4. Kubernetes side — the tunnel token Secret

```bash
kubectl -n ci-tunnel create secret generic ci-tunnel-token \
  --from-literal=TUNNEL_TOKEN='<paste the token from step 1>'
```

Then apply, in order:

```bash
kubectl apply -f ci-tunnel/00-namespace.yaml
kubectl apply -f ci-tunnel/10-serviceaccount.yaml
kubectl apply -f ci-tunnel/20-deployment.yaml
kubectl -n ci-tunnel get pods   # expect 1/1 Running
kubectl -n ci-tunnel logs deploy/cloudflared | grep -i "registered tunnel"
```

## 5. GitHub side — wire the service token into both repos

```bash
gh secret set CF_ACCESS_CLIENT_ID     --repo ChayzX/k8s-homelab --body '<client id>'
gh secret set CF_ACCESS_CLIENT_SECRET --repo ChayzX/k8s-homelab --body '<client secret>'
gh secret set CF_ACCESS_CLIENT_ID     --repo ChayzX/pantry-bot  --body '<client id>'
gh secret set CF_ACCESS_CLIENT_SECRET --repo ChayzX/pantry-bot  --body '<client secret>'
```

(Same token, both repos — it's the same k8s API endpoint either way. Give
each repo its own Service Token later if you ever want to be able to revoke
one workflow's access without affecting the other.)

## 6. Workflow side — the client proxy pattern

Already implemented and live -- see the actual `deploy` jobs for the exact
steps: `.github/workflows/opsbot-deploy.yml` (this repo) and
`.github/workflows/deploy.yml` (pantry-bot repo). Both follow the same
shape: install `cloudflared` (pinned version + sha256 checksum, not
`latest` -- see that step's own comment for why), open the Access-
authenticated tunnel via `cloudflared access tcp` (service token passed as
env vars `TUNNEL_SERVICE_TOKEN_ID`/`TUNNEL_SERVICE_TOKEN_SECRET`, not CLI
args -- args are visible in the runner's process table), write the scoped
kubeconfig from a base64 GitHub Actions secret (`KUBE_CONFIG_OPSBOT` /
`KUBE_CONFIG_PANTRYBOT`) to `$RUNNER_TEMP`, pointed at
`https://127.0.0.1:16443`, then the `kubectl` steps.

`cloudflared access tcp` does the Access handshake once and then proxies raw
bytes — kubectl never needs to know Access exists, it just talks to
`127.0.0.1:16443` like it's local. The scoped `ci-deploy` ServiceAccount
kubeconfig from `../ci-deploy/` is unchanged; only the `server:` field points
at the local proxy port instead of the cluster directly.

## 7. Verify before trusting it

```bash
kubectl -n ci-tunnel logs deploy/cloudflared --tail=50
```

confirm connections are only ever coming from the two workflow runs you
trigger, not from anywhere else — an unexpected caller here means the Access
policy is misconfigured (e.g. accidentally "Everyone" instead of the service
token).

## 8. Oracle standby credential fence

The Oracle connector must be provisioned from the repository's separate
`ci-tunnel-oracle/` overlay, not by scaling the home `cloudflared` Deployment.
Before enabling it, require all of the following in Issue #204:

- an independently named Oracle Cloudflare tunnel and API hostname/Access
  application;
- a separate Oracle tunnel token stored only as
  `ci-tunnel-token-oracle` in the Oracle cluster;
- a separately scoped Oracle Access service token and least-privilege Oracle
  kubeconfig in GitHub Actions; and
- a non-production target-identity and rollback run that proves the selected
  credentials can mutate Oracle and cannot mutate home.

Never reuse `ci-tunnel-token`, the home Access service token, or the home
kubeconfig for Oracle. Route creation and token issuance are external to this
repository; record only their identifiers and verification results, never
secret values.

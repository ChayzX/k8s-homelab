# ci-tunnel — manual setup

Everything in this directory (`00-namespace.yaml`, `10-serviceaccount.yaml`,
`20-deployment.yaml`) is the connector *pod*. The tunnel itself, its public
hostname, and Cloudflare Access are dashboard-managed — same convention as
every other tunnel in this repo (see `../pantry-bot/60-deployment-cloudflared.yaml`'s
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

Once the above is live, each `deploy` job (currently stubbed with a
`# TODO(k8s-homelab-oiv.8)` comment in both workflows) needs, before the
`kubectl` steps:

```yaml
      - name: Install cloudflared
        run: |
          curl -sSL -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
          chmod +x cloudflared
          sudo mv cloudflared /usr/local/bin/

      - name: Open Access-authenticated tunnel to k8s API
        run: |
          cloudflared access tcp \
            --hostname k8s-api.greeniespantry.uk \
            --url 127.0.0.1:6443 \
            --service-token-id "${{ secrets.CF_ACCESS_CLIENT_ID }}" \
            --service-token-secret "${{ secrets.CF_ACCESS_CLIENT_SECRET }}" &
          sleep 3   # give the local listener a moment before kubectl steps

      - name: Update deployment image
        env:
          KUBECONFIG: ...   # server: https://127.0.0.1:6443, same CA/token as today
        run: kubectl set image ...
```

`cloudflared access tcp` does the Access handshake once and then proxies raw
bytes — kubectl never needs to know Access exists, it just talks to
`127.0.0.1:6443` like it's local. The scoped `ci-deploy` ServiceAccount
kubeconfig from `../ci-deploy/` is unchanged; only the `server:` field and
this proxy step are new.

## 7. Verify before trusting it

```bash
kubectl -n ci-tunnel logs deploy/cloudflared --tail=50
```

confirm connections are only ever coming from the two workflow runs you
trigger, not from anywhere else — an unexpected caller here means the Access
policy is misconfigured (e.g. accidentally "Everyone" instead of the service
token).

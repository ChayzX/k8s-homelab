# mcp-grafana — manual setup

`mcp-grafana.yaml` and `mcp-grafana-cloudflared.yaml` are the Kubernetes side
(the MCP server pod and its dedicated tunnel connector). The tunnel itself,
its public hostname, and its Cloudflare Access policy are dashboard-managed —
same convention as every other tunnel in this repo (see
`../ci-tunnel/MANUAL-SETUP.md`). This session has no Cloudflare API
credentials, only what's checked into git, so these steps can't be scripted
from here.

## 1. Create the Grafana service account token (if you don't already have one)

Self-hosted Grafana -> Administration -> Service accounts -> new service
account -> Add token. Grant it the narrowest role that covers the MCP tools
you want available (read-only Viewer is enough for querying
dashboards/datasources; broader roles only if you want the agent creating or
editing dashboards).

## 2. Kubernetes secrets

```bash
kubectl -n observability create secret generic mcp-grafana-grafana-token \
  --from-literal=token='<glsa_... service account token>'

kubectl -n observability create secret generic mcp-grafana-server-token \
  --from-literal=token="$(openssl rand -hex 32)"
```

See `SECRETS.md` items 5–6 for the full contract on each.

## 3. Apply the MCP server

```bash
kubectl apply -f observability/mcp-grafana.yaml
kubectl -n observability get pods -l app.kubernetes.io/name=mcp-grafana
```

Expect `1/1 Running`. If it crash-loops on a permission error, see the
`securityContext` comment in `mcp-grafana.yaml` about the `runAsUser: 65532`
assumption.

## 4. Verify Grafana auth works, in-cluster, before wiring up the tunnel

```bash
kubectl -n observability port-forward svc/mcp-grafana 8000:8000
```

In another terminal:

```bash
curl -sS http://localhost:8000/healthz
# expect: ok

curl -sS -X POST http://localhost:8000/mcp \
  -H 'Authorization: Bearer <mcp-grafana-server-token value>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"0.0.1"}}}'
# expect a JSON-RPC result, not an auth error or an HTML login-page redirect
```

If this second call fails with something that looks like an HTML login page
rather than JSON, `GRAFANA_URL`/`GRAFANA_SERVICE_ACCOUNT_TOKEN` is the
problem, not the tunnel — fix that before going further, since every layer
below this one assumes Grafana auth already works.

## 5. Cloudflare dashboard — new tunnel (Zero Trust → Networks → Tunnels)

New tunnel, name it something like `k8s-homelab-mcp-grafana`. **Do not reuse**
an existing tunnel (the one fronting `grafana.greeniespantry.uk` included) —
same blast-radius reasoning as `ci-tunnel/MANUAL-SETUP.md` step 1. Copy the
generated token for step 7.

Public hostname: `mcp-grafana.greeniespantry.uk`, origin
`http://mcp-grafana.observability.svc.cluster.local:8000`.

## 6. Cloudflare Access — Service Token, NOT Authentik

Zero Trust -> Access -> Service Auth -> create a Service Token (e.g.
`claude-mcp-grafana`). Note the **Client ID** and **Client Secret** — shown
once.

Then add an Access Application for the `mcp-grafana.greeniespantry.uk`
hostname with a policy allowing *only* that service token (Action: Service
Auth). **Do not** attach the Authentik outpost/IdP policy used by
`grafana.greeniespantry.uk` here — that's the one thing this whole setup
exists to avoid (see `mcp-grafana-cloudflared.yaml`'s header for the full
reasoning: Authentik's outpost redirects unauthenticated requests to a login
page, which breaks a Bearer-token MCP client the same way it broke the
Grafana Cloud MCP connector).

If your MCP client can't be configured to send the
`CF-Access-Client-Id`/`CF-Access-Client-Secret` headers a Service Token
requires (check whatever's driving the "reconfigure the connector on Claude's
side" step), the alternative fitting this stack is an **IP-based Access
policy** scoped to your Tailscale CGNAT range or home egress IP instead of a
Service Token — same Access Application, different policy rule. Either way,
the one non-negotiable is: no Authentik/IdP policy on this hostname.

## 7. Kubernetes side — the tunnel token Secret

```bash
kubectl -n observability create secret generic mcp-grafana-cloudflared-tunnel-token \
  --from-literal=TUNNEL_TOKEN='<paste the token from step 5>'

kubectl apply -f observability/mcp-grafana-cloudflared.yaml
kubectl -n observability get pods -l app.kubernetes.io/name=mcp-grafana-cloudflared
kubectl -n observability logs deploy/mcp-grafana-cloudflared | grep -i "registered tunnel"
```

## 8. Verify from outside the cluster

```bash
# Without credentials -- expect Cloudflare Access to block this (302 to an
# Access login/verification page, or 403), NOT a 200.
curl -sS -o /dev/null -w '%{http_code}\n' https://mcp-grafana.greeniespantry.uk/healthz

# With the Access service token headers -- expect 200 / "ok".
curl -sS \
  -H "CF-Access-Client-Id: <client id from step 6>" \
  -H "CF-Access-Client-Secret: <client secret from step 6>" \
  https://mcp-grafana.greeniespantry.uk/healthz
```

Then repeat the `initialize` POST from step 4 against
`https://mcp-grafana.greeniespantry.uk/mcp`, with both the Access headers and
the `Authorization: Bearer <mcp-grafana-server-token>` header. That's the
full path (Cloudflare Access -> mcp-grafana's own token check -> Grafana
service account auth) exercised end to end.

## 9. Point Claude's connector at it

- URL: `https://mcp-grafana.greeniespantry.uk/mcp`
- Transport: Streamable HTTP
- Auth headers: whatever the connector config supports — at minimum the
  `Authorization: Bearer <mcp-grafana-server-token>` header from step 2;
  add the `CF-Access-*` headers too if the connector lets you set arbitrary
  headers, otherwise the IP-allowlist fallback from step 6 is the option that
  doesn't need them.

Confirm connections in `mcp-grafana-cloudflared`'s logs are only ever coming
from calls you actually trigger, same verification spirit as
`ci-tunnel/MANUAL-SETUP.md` step 7 — an unexpected caller here means the
Access policy is misconfigured.

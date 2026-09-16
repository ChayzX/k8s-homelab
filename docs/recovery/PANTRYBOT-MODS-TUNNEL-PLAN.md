# PantryBot moderator console (mods) dedicated Cloudflare Tunnel plan

Status: **planned, not yet cut over**. `mods.greeniespantry.uk` is intended to
move to a dedicated active-active dual-site tunnel with one cloudflared
connector on home and one on Oracle, mirroring the established
`commands.greeniespantry.uk` pattern (see
[PANTRYBOT-COMMANDS-TUNNEL-PLAN.md](./PANTRYBOT-COMMANDS-TUNNEL-PLAN.md)).
`overlay.greeniespantry.uk` stays **OFF** in this pass (`route_state
standby-excluded` and absent from the route adapter `application_routes`) until
equivalent home and Oracle overlay capacity and WebSocket reconnect tests pass.

This document is the repository-only plan. No live Cloudflare, DNS, SSH,
Kubernetes, or timer change has been made: it records the required entries, the
cutover/rollback order, and the adapter inputs target state so the operator can
execute the cutover from the documented evidence alone.

## Current mods delivery (read from the checked-in adapter inputs)

The route adapter inputs
(`observability/failover-witness/cloudflare-route-inputs.example.json`) currently
switch only `mods`/`overlay` between per-site application tunnels:

- `oracle` uses the per-site application tunnel
  `30dde1eb-e625-4dd4-b5ac-2d385a1b7336`, `route_state: standby-excluded`.
- `canada` (the home-side connector site name used by the adapter/promoter)
  uses the connector tunnel `8392cd48-c1bf-437b-ac03-3c4b4acd321e`
  (`route_state: active`) with origins `http://127.0.0.1:18081` (mods) and
  `http://127.0.0.1:18082` (overlay).

`commands.greeniespantry.uk` is already on the dedicated active-active
`PantryBot-Commands` tunnel `c0015a8b-3f9e-4af9-b172-a97b882b4b28`, and
`oauth.greeniespantry.uk` stays on the shared `PantryBot` tunnel
`59569621-7067-4146-a0e8-5ed84b7f9538` under the single home Authentik writer
decision (#329). The historical shared-tunnel inventory recorded in the
commands plan (2026-09-11) still listed a `mods` ingress object; the current
live shared configuration and the live `mods` DNS target must be re-read by the
operator before cutover and preserved as the rollback source.

## Target topology

Create a new dedicated remotely-managed tunnel (unique name, for example
`pantrybot-mods-only` / `PantryBot-Mods`), exactly one connector per site:

- **Home** cluster: `mods-cloudflared` Deployment, `replicas: 1`.
- **Oracle** cluster: the same `mods-cloudflared` Deployment.

Apply the same manifest once per cluster (DNS resolution is local to each
cluster). The manifest mirrors `pantry-bot/62-deployment-commands-cloudflared.yaml`:

- connector Deployment `mods-cloudflared` (namespace `pantry-bot`) using the
  pinned multi-architecture image
  `cloudflare/cloudflared:2026.7.3@sha256:e39ee8da81ad5e05d77f38d2f51c60ca51bf2a8450ac3abab50c17fdb91d91bf`
  (same digest as the commands connector; no tag-only production pull).
- dedicated ServiceAccount `mods-cloudflared-sa` added to
  `pantry-bot/10-serviceaccounts.yaml` with no Role/RoleBinding and
  `automountServiceAccountToken: false`.
- `TUNNEL_TOKEN` from the independently provisioned Secret
  `mods-cloudflared-tunnel-token` (never commit the token; no shared-Secret
  reuse with the commands/shared/app connectors).
- `--metrics 0.0.0.0:2000` with the `/ready` readiness/liveness probes and
  `TUNNEL_TRANSPORT_PROTOCOL=http2`, matching the commands connector so QUIC/UDP
  timeouts cannot differ between sites during failover.

The tunnel's control-plane ingress must contain exactly the moderator console
hostname plus the terminal 404:

```json
{
  "config": {
    "ingress": [
      {
        "hostname": "mods.greeniespantry.uk",
        "service": "http://pantry-private-site.pantry-bot.svc.cluster.local:3000"
      },
      { "service": "http_status:404" }
    ]
  }
}
```

DNS is a proxied CNAME:

```text
mods.greeniespantry.uk -> <MODS_TUNNEL_ID>.cfargotunnel.com
```

Do not add `oauth`, `commands`, `overlay`, Authentik, SSH, RDP, Minecraft, the
Kubernetes API, or any wildcard to this tunnel. Do not add a Cloudflare Access /
Access Application policy to the public mods route. The private
`pantry-private-site` still proxies `/mod/api`, `/login`, and `/oauth/callback`
to `pantry-private-api` inside the cluster; that in-cluster OAuth callback is
managed by the API origin and is not the public `oauth.greeniespantry.uk`
hostname. The single home Authentik writer decision and the public OAuth route
are untouched.

## Route adapter target state (inputs example)

`observability/failover-witness/cloudflare-route-inputs.example.json` changes so
the adapter stays consistent with the dual-site mods model:

- **mods is removed from the per-site `application_routes`.** The adapter would
  otherwise keep flipping `mods` between per-site tunnels and 404-ing whichever
  site is standby, which contradicts an active-active dedicated tunnel and would
  also raise the adapter's "tunnel assigned to multiple sites" guard if both
  sites referenced one tunnel. Like `commands`, the mods tunnel is managed
  outside the adapter.
- **overlay is absent from `application_routes`** and both sites keep
  `route_state: standby-excluded`. The adapter ignores `route_state`, so leaving
  `overlay` in the active site's `application_routes` would republish the overlay
  origin whenever a site is promoted; only removing it from the routes guarantees
  overlay stays OFF this pass. The `route_state` field remains documentary.
- **oauth exclusion is intact**: no oauth hostname may appear in
  `application_routes` or `commands_routes`; `oauth.greeniespantry.uk` remains on
  the shared tunnel (#329).
- The per-site tunnel identifiers (`oracle` application tunnel
  `30dde1eb-...`, `canada` connector tunnel `8392cd48-...`) remain so the
  adapter can park each to the `http_status:404` standby and be re-armed for
  overlay switching after the capacity pass, but their route lists are empty.

Both sites therefore produce a 404-only desired config; whichever site is
`--active`, the adapter parks both tunnels rather than publishing mods/overlay.
`commands.greeniespantry.uk` and `oauth.greeniespantry.uk` do not appear in the
inputs, preserving the live split.

## Sequencing and gates (do not shortcut)

This pass must NOT enable overlay, duplicate voice/commands, or change
authentication.

0. **Overlay stays OFF.** `overlay.greeniespantry.uk` remains `route_state
   standby-excluded` and is not present in any `application_routes` in this
   pass. Do not enable it on either site until equivalent home and Oracle
   overlay capacity and WebSocket reconnect tests pass (see
   [PANTRYBOT-PUBLIC-ROUTING.md](./PANTRYBOT-PUBLIC-ROUTING.md)).
1. With an authenticated, least-privilege Cloudflare OAuth/MCP capability, create
   the new remotely-managed tunnel but do not change DNS or any existing tunnel.
   Record the returned tunnel UUID. Do not reuse the shared, commands, or
   per-site tunnel UUIDs or tokens.
2. Set the new tunnel's remotely managed ingress to the exact target JSON above
   while DNS still points at the current mods target. Use the Tunnel
   Configuration API/MCP, not the dashboard Published-application wizard: the
   wizard can create the CNAME automatically, whereas API-created routes require
   a separate DNS change. This separation is the rollback-safe staging boundary.
3. Copy the new token into the operator's password manager or a secure shell
   prompt; never commit it, place it in a ConfigMap, pass it as an argument, or
   paste it into an issue. Create the Secret independently in home and Oracle:
   `kubectl -n pantry-bot create secret generic mods-cloudflared-tunnel-token
   --from-literal=TUNNEL_TOKEN="$MODS_TUNNEL_TOKEN" --dry-run=client -o yaml |
   kubectl apply -f -` (add `--kubeconfig "$ORACLE_KUBECONFIG"` for Oracle), then
   `unset MODS_TUNNEL_TOKEN` and verify only the key name.
4. Add `mods-cloudflared-sa` and deploy exactly one `mods-cloudflared` connector
   per cluster. Keep it separate from the shared, commands, and app connectors;
   do not edit or restart them.
5. Before DNS, verify both connectors are Ready and registered to the new tunnel
   UUID; port-forward each local `pantry-private-site` Service and verify
   `/mod/`, `/login/mod`, and `/mod/api/commands` against the API origin. Check
   only connector log tunnel-UUID/registration lines; never print the Secret.
6. Capture the existing `mods.greeniespantry.uk` CNAME record (ID, name, target,
   proxied state, TTL, comment, tags) and, if the live shared tunnel still has a
   `mods` ingress object, its full configuration. Preserve both as
   operator-only files; they are the rollback source.
7. PATCH only the `mods.greeniespantry.uk` proxied CNAME target to
   `<MODS_TUNNEL_ID>.cfargotunnel.com`, preserving the record ID, name, proxied
   state, TTL, comment, and tags. Do not change any other DNS record.
8. Run every external check in the verification section. If any fails, patch the
   one CNAME back to the saved target (the old per-site tunnels are still
   intact at this stage, so rollback is one DNS PATCH with no tunnel write).
9. Only after all checks pass, mechanically remove the single `mods` ingress
   object from the saved shared configuration (if present) and PUT the complete
   remainder back, exactly as the commands plan did for `commands`: verify the
   hostname matches exactly one object with `jq -e`, generate the candidate with
   `jq --arg hostname mods.greeniespantry.uk`, and confirm an order-preserving
   diff removes only that one object.
10. Deploy the updated route adapter inputs (the target JSON in this plan) to the
    live `/etc/failover-witness/cloudflare-route-inputs.json` and run the adapter
    once with `--active` set to the current promotion site so both per-site
    tunnels are parked at `http_status:404`. Verify `mods.greeniespantry.uk`
    still resolves through the dedicated tunnel and `overlay.greeniespantry.uk`
    is not exposed.
11. Leave the shared, commands, and app connectors running. Do not delete any
    tunnel, Secret, or Deployment.

## Rollback

- **Before step 10** (old per-site tunnels still published): one DNS PATCH of
  `mods.greeniespantry.uk` back to the saved pre-cutover target restores the
  prior route. No tunnel configuration write is required.
- **After step 10**: restore the previous adapter inputs, dry-run then `--apply`
  to republish the old per-site tunnels, patch the CNAME back to the saved
  target, verify `/mod/` returns the legacy route, then scale both
  `mods-cloudflared` Deployments to zero. Retain the new tunnel and both Secrets
  for inspection; removing the dedicated ingress is optional cleanup only after
  the rollback is healthy.
- Never roll back by enabling `overlay` and never repoint DNS at an overlay or
  commands origin.

## Safety notes

- Do not duplicate voice or commands: `commands.greeniespantry.uk` remains on
  `c0015a8b-3f9e-4af9-b172-a97b882b4b28` with its two connectors; do not add the
  commands hostname or a second commands connector to this work.
- No authentication change: `oauth.greeniespantry.uk` stays on the shared tunnel
  under the single home Authentik writer decision (#329). The adapter inputs
  must never contain the oauth hostname (the
  `tests/pantrybot-routing-contract-test.sh` gate enforces this). The mods
  private UI's in-cluster `/oauth/callback` proxy is unchanged and is not the
  public oauth hostname.
- No overlay enablement this pass; overlay remains `standby-excluded`/absent
  until the capacity and WebSocket reconnect gates pass.
- Tokens are Secret-file only; no connector environment, CLI argument, or
  repository copy.

## Verification after cutover

From an external observer, record status, content type, and response-body
checksums in the tracking issue:

```bash
set -eu
base=https://mods.greeniespantry.uk
test "$(curl --max-time 15 --max-redirs 0 -sS -o /tmp/pantry-mods-root -w '%{http_code}' "$base/mod/")" = 200
# /login/mod stays reachable through the API origin (previous evidence: HTTP 302).
curl --max-time 15 --max-redirs 0 -sS -o /tmp/pantry-mods-login -w '%{http_code}\n' "$base/login/mod"
curl --max-time 15 --max-redirs 0 -sS -o /tmp/pantry-mods-api -w '%{http_code}\n' "$base/mod/api/commands"
sha256sum /tmp/pantry-mods-root /tmp/pantry-mods-login /tmp/pantry-mods-api
```

Also verify, without changing them:

- `commands.greeniespantry.uk/` and `/api/public/commands` still return HTTP 200
  and no login surface.
- `oauth.greeniespantry.uk/login/broadcaster` still reaches the Authentik login
  flow.
- `overlay.greeniespantry.uk/` is not exposed after the adapter inputs deploy
  (overlay OFF this pass).
- Cloudflare reports the new tunnel healthy with edge connections spanning home
  and Oracle, and both connector pods `Ready`.

Do not test or alter Minecraft, SSH, RDP, Kubernetes API, or other shared routes
as part of this change.

## Open decisions for the implementing workstream

- Physical tunnel: create a brand-new `pantrybot-mods-only` tunnel (recommended,
  mirrors commands) rather than reusing the oracle per-site application tunnel or
  the canada connector tunnel.
- Tunnel UUID to record in the inputs notes and routing contract once created.
- Verify the current live `mods.greeniespantry.uk` CNAME target and whether the
  shared tunnel still contains a `mods` ingress object before cutover.
- The operator-supplied Cloudflare account ID / scoped token for the cutover.

## References

- [PantryBot public routing contract](./PANTRYBOT-PUBLIC-ROUTING.md)
- [PantryBot commands-only tunnel plan](./PANTRYBOT-COMMANDS-TUNNEL-PLAN.md)
- [Cloudflare route adapter contract](../../observability/failover-witness/README.md)
- [Cloudflare: deploy cloudflared connectors in Kubernetes](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflared/deployment-guides/kubernetes/)
- [Cloudflare: tunnel replicas](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflared/configure-tunnels/tunnel-availability/deploy-replicas/)
- [Cloudflare: tunnel DNS records](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflared/routing-to-tunnel/dns/)
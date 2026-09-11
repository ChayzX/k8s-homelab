# PantryBot commands-only Cloudflare Tunnel plan

Status: repository preparation complete; live Cloudflare cutover blocked on an
authenticated Cloudflare mutation capability. This is the safe routing plan
for moving the viewer-facing commands site without changing the existing
shared tunnel or any private/Minecraft route.

## Read-only evidence captured 2026-09-11

- The live shared, remotely-managed tunnel UUID is
  `59569621-7067-4146-a0e8-5ed84b7f9538`.
- The `pantry-bot/cloudflared` Deployment has two ready connectors, one on
  `chasebot` and one on `minecraftmachine`.
- Its live ingress contains `oauth`, `grafana`, `operations`, `auth`,
  `overlay`, `bead`, `k8s-api`, `ssh`, `rdp`, `cartwise`, `commands`, and
  `mods` hostnames, followed by an HTTP 404 catch-all. The live log also shows
  the `bead` route still points at the retired Scotty service; that unrelated
  issue is deliberately out of scope here.
- `commands.greeniespantry.uk` currently points at the legacy PantryBot
  service. Fresh external checks returned:
  - `/` -> HTTP 200, legacy PantryBot HTML, SHA-256
    `09d499e39606aa70428b3b0d037d6123be8ba1b470fa8bdd3586e5eff1149089`
  - `/api/public/commands` -> HTTP 404 (`Cannot GET`), SHA-256
    `92ff9ca6df6b681dde00dbdc7c92edc8f26ca6af0b34373312579ad2dc039c9d`
  - `/login/broadcaster` -> HTTP 200, OAuth page, SHA-256
    `d714f8102e93fa1d52ac0f3e68564641bdb7e04f4031d9ab0c8b08adb2500c41`
  - `oauth.greeniespantry.uk/login/broadcaster` -> HTTP 200
  - `mods.greeniespantry.uk/mod/` -> HTTP 302 to `/login/mod`
- The home `pantry-commands-site` Service is internal ClusterIP only and has
  two ready pods. A direct Service port-forward returned HTTP 200 from `/` and
  `/api/public/commands`, with 13 command items, and HTTP 404 from
  `/login/broadcaster`. The independent Oracle environment previously passed
  the same Deployment/Service contract with two ready ARM64 replicas; its
  current management SSH key was not available to this session for a fresh
  read-only recheck.
- The authenticated tool inventory exposed GitHub issue mutation, but no
  Cloudflare tunnel create, tunnel configuration, token, DNS, or Access
  mutation. No Cloudflare, DNS, tunnel, Secret, connector, or production route
  mutation was performed. The checked-in connector is
  `pantry-bot/62-deployment-commands-cloudflared.yaml`.

The UUID and route inventory above came from the live `cloudflared` log and
Kubernetes read-only inspection, not from a tunnel token. The token was never
read or printed.

## Target configuration

Create a new remotely-managed tunnel named `pantrybot-commands-only` (or an
equivalent unique name). It must have exactly one published application route:

```json
{
  "config": {
    "ingress": [
      {
        "hostname": "commands.greeniespantry.uk",
        "service": "http://pantry-commands-site.pantry-bot.svc.cluster.local:3000"
      },
      { "service": "http_status:404" }
    ]
  }
}
```

Do not add `oauth`, `mods`, `overlay`, Authentik, SSH, RDP, Kubernetes API,
Minecraft, or any wildcard hostname to this tunnel. Do not add a Cloudflare
Access policy to the public commands route. The commands service itself is the
read-only, OAuth-free boundary; it owns `/` and `/api/public/commands` and
returns no OAuth/login surface.

The same tunnel token may be stored as
`pantry-bot/commands-cloudflared-tunnel-token` in each independent PantryBot
cluster. Each connector resolves the same service name inside its own local
cluster; no private ClusterIP is published to DNS.

## Safe preparation and cutover order

1. Using an authenticated, least-privilege Cloudflare OAuth/MCP capability,
   create the new remotely-managed tunnel but do not change DNS or the shared
   tunnel. Record the returned tunnel UUID. Do not use the shared tunnel UUID
   or token.
2. Set the new tunnel's remotely managed ingress to the exact target JSON
   above while DNS still points to the shared tunnel. Use the Tunnel
   Configuration API/MCP, not the dashboard Published application wizard:
   Cloudflare documents that dashboard-created published applications can
   create the CNAME automatically, while API-created routes require a separate
   DNS change. This separation is the rollback-safe staging boundary.
3. Copy the new tunnel token into the operator's password manager or a secure
   shell prompt. Never commit it, place it in a ConfigMap, pass it as a CLI
   argument, or paste it into an issue.
4. Create the token Secret independently in home and Oracle. This command does
   not print or persist the token in the repository or shell history:

   ```bash
   read -r -s -p 'commands-only tunnel token: ' COMMANDS_TUNNEL_TOKEN; echo
   kubectl -n pantry-bot create secret generic commands-cloudflared-tunnel-token \
     --from-literal=TUNNEL_TOKEN="$COMMANDS_TUNNEL_TOKEN" \
     --dry-run=client -o yaml | kubectl apply -f -
   unset COMMANDS_TUNNEL_TOKEN
   ```

   Run it once against each cluster context. On Oracle, add
   `--kubeconfig "$ORACLE_KUBECONFIG"` to both `kubectl` invocations. Verify
   only the key name:

   ```bash
   kubectl -n pantry-bot get secret commands-cloudflared-tunnel-token \
     -o json | jq -r '.data | keys[]'
   ```

5. Deploy exactly one separate connector in each cluster. The manifest is
   identical because DNS resolution remains local to each cluster:

   ```bash
   kubectl apply -f pantry-bot/10-serviceaccounts.yaml
   kubectl apply -f pantry-bot/62-deployment-commands-cloudflared.yaml
   kubectl --kubeconfig "$ORACLE_KUBECONFIG" apply \
     -f pantry-bot/10-serviceaccounts.yaml
   kubectl --kubeconfig "$ORACLE_KUBECONFIG" apply \
     -f pantry-bot/62-deployment-commands-cloudflared.yaml
   ```

   Keep it separate from the existing `cloudflared` Deployment; do not edit or
   restart the shared connector.
6. Before changing DNS, verify both new connectors are Ready and registered to
   the new tunnel UUID, and port-forward each local
   `pantry-commands-site` Service to verify `/`, `/api/public/commands`, and the
   `/login/broadcaster` 404. Check only the connector log's tunnel UUID and
   registration lines; never print the Deployment environment or Secret.
   Re-read the new tunnel configuration and prove that its ingress array is
   exactly the commands hostname plus `http_status:404`.
7. Capture the existing shared configuration and the current commands DNS
   record immediately before cutover. Preserve both as operator-only files;
   do not reconstruct either from this document:

   ```bash
   export CF_ACCOUNT_ID='<operator-supplied account id>'
   read -r -s -p 'least-privilege Cloudflare token: ' CF_API_TOKEN; echo
   export SHARED_TUNNEL_ID='59569621-7067-4146-a0e8-5ed84b7f9538'
   curl --fail-with-body -sS \
     "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$SHARED_TUNNEL_ID/configurations" \
     -H "Authorization: Bearer $CF_API_TOKEN" \
     -H 'Content-Type: application/json' \
     -o "shared-tunnel-$SHARED_TUNNEL_ID-before.json"
   chmod 600 "shared-tunnel-$SHARED_TUNNEL_ID-before.json"
   unset CF_API_TOKEN
   ```

   Use a token scoped to tunnel inspection and the `greeniespantry.uk` zone;
   never use a Global API Key. Record the existing CNAME record ID, target,
   proxied state, and TTL without changing them.
8. Move only `commands.greeniespantry.uk` by patching its existing proxied
   CNAME target from
   `59569621-7067-4146-a0e8-5ed84b7f9538.cfargotunnel.com` to
   `<NEW_TUNNEL_ID>.cfargotunnel.com`. Preserve the same record ID, name,
   proxied state, TTL, comment, and tags. Do not remove the commands ingress
   from the shared tunnel yet, and do not change any other DNS record.
9. Run every external check below. If any check fails, immediately patch the
   one CNAME back to the saved shared-tunnel target. Because the shared ingress
   is still intact at this stage, this rollback does not require a tunnel
   configuration write.
10. Only after all external checks pass, mechanically remove the single ingress
    object whose hostname is exactly `commands.greeniespantry.uk` from the
    saved full shared configuration and PUT the complete remainder back to the
    shared tunnel. Preserve ordering and every other ingress/origin setting,
    including the terminal 404. Before the PUT, require exactly one matching
    object and generate the candidate rather than editing by hand:

    ```bash
    jq -e --arg hostname commands.greeniespantry.uk \
      '(.result.config.ingress | map(select(.hostname? == $hostname)) | length) == 1' \
      "shared-tunnel-$SHARED_TUNNEL_ID-before.json" >/dev/null
    jq --arg hostname commands.greeniespantry.uk \
      '{config: (.result.config | .ingress |= map(select(.hostname? != $hostname)))}' \
      "shared-tunnel-$SHARED_TUNNEL_ID-before.json" \
      > "shared-tunnel-$SHARED_TUNNEL_ID-without-commands.json"
    chmod 600 "shared-tunnel-$SHARED_TUNNEL_ID-without-commands.json"
    ```

    Re-read both tunnel configurations and verify the commands hostname occurs
    only on the dedicated tunnel. Compare the shared configuration before and
    after with an order-preserving JSON diff; the only removed value must be
    the one matching ingress object.
11. Leave the existing shared connectors and Secret running. They must retain
    every private/Auth/SSH/RDP/monitoring/CI/Minecraft route. Do not delete the
    shared tunnel, its Secret, the dedicated tunnel, or either dedicated
    Secret.

The Cloudflare API endpoints for this operation are the read/update
configuration endpoints under
`/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations`. A PUT replaces
the tunnel configuration, so the saved shared JSON is the rollback source and
must be edited mechanically to remove only the one `commands` hostname. Do not
use a hand-written replacement for the shared tunnel. The DNS cutover is a
PATCH of the existing record, not a delete/create pair.

## Verification after cutover

Run these checks from an external observer and record status, content type,
and response body checksums in issue #148:

```bash
set -eu
base=https://commands.greeniespantry.uk
test "$(curl --max-redirs 0 -sS -o /tmp/pantry-commands-root -w '%{http_code}' "$base/")" = 200
test "$(curl --max-redirs 0 -sS -o /tmp/pantry-commands-api -w '%{http_code}' "$base/api/public/commands")" = 200
test "$(curl --max-redirs 0 -sS -o /tmp/pantry-commands-login -w '%{http_code}' "$base/login/broadcaster")" = 404
! rg -qi 'oauth|login|session|twitch credential' /tmp/pantry-commands-root /tmp/pantry-commands-api
jq -e '.items | type == "array" and length > 0' /tmp/pantry-commands-api >/dev/null
sha256sum /tmp/pantry-commands-root /tmp/pantry-commands-api /tmp/pantry-commands-login
```

Also verify that `oauth.greeniespantry.uk/login/broadcaster` remains reachable
and that `mods.greeniespantry.uk/mod/` still redirects to `/login/mod`. Do not
test or alter Minecraft, SSH, RDP, Kubernetes API, or any other shared route as
part of this change.

Before shared-tunnel contraction, rollback is one DNS PATCH back to
`59569621-7067-4146-a0e8-5ed84b7f9538.cfargotunnel.com`. After contraction,
first restore the exact saved shared configuration (including the original
commands ingress), then patch DNS back, verify the legacy route, and scale both
new connector Deployments to zero. Retain the dedicated tunnel and both
Secrets for inspection; do not delete them. Removing the dedicated ingress is
optional cleanup only after the rollback is healthy.

## References

- [PantryBot public routing contract](./PANTRYBOT-PUBLIC-ROUTING.md)
- [PantryBot issue #148](https://github.com/ChayzX/pantry-bot/issues/148)
- [Cloudflare: create a remotely-managed tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)
- [Cloudflare: Kubernetes deployment](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/deployment-guides/kubernetes/)
- [Cloudflare: tunnel replicas](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-availability/deploy-replicas/)
- [Cloudflare: tunnel DNS records](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/)
- [Cloudflare: tunnel configuration API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/subresources/configurations/)
- [Cloudflare: DNS record PATCH API](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/edit/)

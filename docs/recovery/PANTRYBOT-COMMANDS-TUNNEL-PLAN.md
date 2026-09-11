# PantryBot commands-only Cloudflare Tunnel plan

Status: prepared, not applied. This is the safe routing plan for moving the
viewer-facing commands site without changing the existing shared tunnel or any
private/Minecraft route.

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
  service. External checks returned:
  - `/` -> HTTP 200, legacy PantryBot HTML
  - `/api/public/commands` -> HTTP 404 (`Cannot GET`)
  - `/login/broadcaster` -> HTTP 200, OAuth page
  - `oauth.greeniespantry.uk/login/broadcaster` -> HTTP 200
  - `mods.greeniespantry.uk/mod/` -> HTTP 302 to `/login/mod`
- The active-active home cluster has two ready
  `pantry-commands-site` pods and the same service contract is present in the
  Oracle overlay. The service is internal ClusterIP only.
- No Cloudflare MCP control was available in this session. No Cloudflare,
  Kubernetes, DNS, tunnel, or production route mutation was performed.

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

The same tunnel token may be stored as a Secret in each independent PantryBot
cluster. Each connector resolves the same service name inside its own local
cluster; no private ClusterIP is published to DNS.

## Safe preparation and cutover order

1. In Cloudflare Zero Trust, create the new remotely-managed tunnel but do not
   add a public hostname yet. Cloudflare's documented flow is Networking >
   Tunnels > Create a tunnel, then add a Published application route on that
   tunnel's Routes tab.
2. Copy the new tunnel token into the operator's password manager or a secure
   shell prompt. Never commit it, place it in a ConfigMap, pass it as a CLI
   argument, or paste it into an issue.
3. Create the token Secret independently in home and Oracle. This command does
   not print or persist the token in the repository or shell history:

   ```bash
   read -r -s -p 'commands-only tunnel token: ' COMMANDS_TUNNEL_TOKEN; echo
   kubectl -n pantry-bot create secret generic commands-cloudflared-tunnel-token \
     --from-literal=TUNNEL_TOKEN="$COMMANDS_TUNNEL_TOKEN" \
     --dry-run=client -o yaml | kubectl apply -f -
   unset COMMANDS_TUNNEL_TOKEN
   ```

   Run it once against each cluster context. Verify only the key name:

   ```bash
   kubectl -n pantry-bot get secret commands-cloudflared-tunnel-token \
     -o json | jq -r '.data | keys[]'
   ```

4. Deploy a separate `commands-cloudflared` Deployment in each cluster using
   the existing pinned `cloudflare/cloudflared:2026.7.3` digest, with
   `TUNNEL_TOKEN` from that Secret, `automountServiceAccountToken: false`,
   `TUNNEL_TRANSPORT_PROTOCOL=http2`, and `/ready` on the metrics port. Keep
   it separate from the existing `cloudflared` Deployment; do not edit or
   restart the shared connector.
5. Before changing the public hostname, verify both new connectors are Ready,
   show the new tunnel UUID in their logs, and can reach the local
   `pantry-commands-site` Service. The dedicated tunnel must have no public
   hostname other than the one below.
6. Capture the existing shared configuration before cutover. Preserve it as
   an operator-only file; do not reconstruct it from this document:

   ```bash
   export CF_ACCOUNT_ID='<operator-supplied account id>'
   export CF_API_TOKEN='<operator-supplied read token>'
   export SHARED_TUNNEL_ID='59569621-7067-4146-a0e8-5ed84b7f9538'
   curl --fail-with-body -sS \
     "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$SHARED_TUNNEL_ID/configurations" \
     -H "Authorization: Bearer $CF_API_TOKEN" \
     -H 'Content-Type: application/json' \
     -o "shared-tunnel-$SHARED_TUNNEL_ID-before.json"
   ```

   The token above is an operator placeholder, not a credential. Do not run
   this command with a token that has more access than needed for inspection.
7. Move only `commands.greeniespantry.uk` from the shared tunnel to the
   dedicated tunnel using the Cloudflare dashboard's Published application
   route editor, or the documented Tunnel Configuration API. If the dashboard
   does not offer an in-place move, perform the two configuration updates in a
   short maintenance window: remove only the `commands` ingress entry from the
   saved shared configuration, then add the target configuration above to the
   dedicated tunnel. Do not change any other shared ingress entry or DNS record.
8. Leave the existing shared connector and its Secret running. It must retain
   all of its other routes. Do not delete the old tunnel or the old Secret.

The Cloudflare API endpoints for this operation are the read/update
configuration endpoints under
`/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations`. A PUT replaces
the tunnel configuration, so the saved shared JSON is the rollback source and
must be edited mechanically to remove only the one `commands` hostname. Do not
use a hand-written replacement for the shared tunnel.

## Verification after cutover

Run these checks from an external observer and record status, content type,
and response body checksums in issue #148:

```bash
set -eu
base=https://commands.greeniespantry.uk
test "$(curl -sS -o /tmp/pantry-commands-root -w '%{http_code}' "$base/")" = 200
test "$(curl -sS -o /tmp/pantry-commands-api -w '%{http_code}' "$base/api/public/commands")" = 200
test "$(curl -sS -o /tmp/pantry-commands-login -w '%{http_code}' "$base/login/broadcaster")" = 404
! rg -qi 'oauth|login|session|twitch credential' /tmp/pantry-commands-root /tmp/pantry-commands-api
jq -e '.items | type == "array"' /tmp/pantry-commands-api >/dev/null
```

Also verify that `oauth.greeniespantry.uk/login/broadcaster` remains reachable
and that `mods.greeniespantry.uk/mod/` still redirects to `/login/mod`. Do not
test or alter Minecraft, SSH, RDP, Kubernetes API, or any other shared route as
part of this change.

For rollback, restore the saved shared configuration with the original
`commands` entry, remove the commands hostname from the dedicated tunnel
configuration, and scale the new connector Deployment to zero. Retain the
dedicated tunnel and its Secret for inspection; do not delete either.

## References

- [PantryBot public routing contract](./PANTRYBOT-PUBLIC-ROUTING.md)
- [PantryBot issue #148](https://github.com/ChayzX/pantry-bot/issues/148)
- [Cloudflare: create a remotely-managed tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)
- [Cloudflare: Kubernetes deployment](https://developers.cloudflare.com/tunnel/deployment-guides/kubernetes/)
- [Cloudflare Tunnel API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/)

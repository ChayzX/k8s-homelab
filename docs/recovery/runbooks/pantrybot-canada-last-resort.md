# PantryBot Canada last-resort manual promotion

Canada is a last-resort recovery site only. **Home and Oracle are always
preferred.** This runbook is for the exceptional case where both home and Oracle
are dark and a PantryBot writer must be recovered from Canada. It is a manual,
operator-confirmed, single-shot exercise; it is never automated and never
scheduled.

## Design invariants

- No automatic controller exists for Canada. No `pantry-postgres-promoter@canada`
  unit is ever installed or enabled (the template unit forbids it).
- `site_neutral_promoter.py` refuses a canada config without
  `--allow-canada-last-resort` and refuses canada **serve mode** entirely:
  flag passes are `--once` only, so a lease is acquired and released within a
  single promotion attempt and is never renewed.
- `scripts/pantrybot-promote-site.sh` refuses `PROMOTION_SITE=canada` unless a
  single-use operator token `CANADA_LAST_RESORT_CONFIRM` is set AND the two
  site-dark probes pass:

  | Variable | Meaning |
  | --- | --- |
  | `CANADA_LAST_RESORT_HOME_GATE` | command that exits 0 only when home is dark / hard-fenced |
  | `CANADA_LAST_RESORT_ORACLE_GATE` | command that exits 0 only when Oracle is dark / hard-fenced |

  The probes are private host-level checks (k3s reachability, StatefulSet pod
  fenced/recovery state, witness lease). A healthy public route is **not** proof
  of a live site.
- `observability/failover-witness/publish-cloudflare-routes.sh` refuses a canada
  route publish without the same `CANADA_LAST_RESORT_CONFIRM` token.
- Canada runs the PostgreSQL standby reached for recovery under WSL/Docker
  (Docker Desktop container `pantrybot-canada-postgres`); the old
  `podman-pantrybot-canada` WSL stack (Fedora 44) is broken and is being
  decommissioned. Do not attempt recovery through Podman.

## Preflight

1. Confirm both preferred sites are dark using the private host gates, not the
   public URL. Example gate commands (home cluster, Oracle cluster):
   `kubectl -n pantry-bot get pod -l statefulset.kubernetes.io/pod-name=postgres-authority-home-return-0` failing,
   `kubectl -n pantry-bot get pod -l app.kubernetes.io/name=...` for the Oracle
   primary absent from endpoints, plus the old-writer fence files
   (`/etc/failover-witness/fence-*.sh`) running success.
2. Generate a one-time token, e.g.
   `CANADA_LAST_RESORT_CONFIRM=$(date +%s%N | sha256sum | cut -c1-32)`. It is an
   acknowledgement of intent, not a secret bound to the recovery.

## Promote Canada (one shot)

On the Canada host, as the operator:

```bash
sudo env \
  PROMOTION_SITE=canada \
  WITNESS_URL=http://127.0.0.1:18765 \
  WITNESS_SHARED_SECRET="${WITNESS_SHARED_SECRET:?}" \
  OLD_WRITER_FENCE_COMMAND='/usr/local/lib/failover-witness/fence-pantry-postgres-oracle.sh --confirm' \
  CANADA_LAST_RESORT_CONFIRM="${CANADA_LAST_RESORT_CONFIRM:?}" \
  CANADA_LAST_RESORT_HOME_GATE='... home-dark probe ...' \
  CANADA_LAST_RESORT_ORACLE_GATE='... oracle-dark probe ...' \
  /usr/local/sbin/pantrybot-promote-site.sh --confirm
```

The adapter runs the promoter once, acquires the `pantry:postgres` lease,
fences the old writer, promotes the local standby, re-points the local Service
endpoint Secret, and restarts the writer deployments. The promoter exits after
the single pass; no timer renews the lease.

## Live routing to Canada (only if required)

Route publication to Canada is last resort and needs the same token:

```bash
sudo env PANTRY_PROMOTION_SITE=canada \
  CANADA_LAST_RESORT_CONFIRM="${CANADA_LAST_RESORT_CONFIRM:?}" \
  /usr/local/bin/publish-cloudflare-routes.sh
```

## Restore normal operation

Restore runs always return routing and authority to home or Oracle — never ask
the auto-failover controller to keep Canada. Promote the preferred site with the
return-home / Oracle promotion sequences in `pantrybot.md`, then verify:
Canada's PostgreSQL is in recovery, the witness lease is back on the preferred
site, `cloudflare-route-inputs.json` shows the preferred site active, and the
restored site passes the overlay WebSocket and oauth checks from
`PANTRYBOT-PUBLIC-ROUTING.md` Validation.

## Verification

- `git log`/issue tracker records the one-shot promotion and the restore.
- The promoter failed closed for Canada unless the token AND both gates passed;
  this is enforced by
  `tests/pantrybot-last-resort-guard-test.sh` in the repo gates.
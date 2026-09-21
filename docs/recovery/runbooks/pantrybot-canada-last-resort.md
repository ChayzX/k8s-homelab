# PantryBot Canada last-resort manual promotion

Canada is a last-resort recovery site only. **Home and Oracle are always
preferred.** This runbook is for the exceptional case where both home and
Oracle are dark and a PantryBot writer must be recovered from Canada. It is
a manual, operator-confirmed, single-shot exercise; it is never automated
and never scheduled.

This replaces an earlier, never-merged draft of the same design
(`site_neutral_promoter.py` on a long-diverged branch) that predates the
current, hardened `oracle_promoter.py` architecture. The dual-gate /
confirm-token pattern is preserved; the mechanism underneath is not.

## Design invariants

- No automatic controller exists for Canada. `authority-gate.ps1`'s
  continuous supervisor Scheduled Task
  (`PantryBot authority gate` / `PantryBot authority supervisor`) is
  deliberately left **Disabled** on the live host. Only `-Once` is ever
  invoked, and only from `canada-last-resort-promote.sh`.
- `canada-last-resort-promote.sh` refuses to run without a single-use
  operator token `CANADA_LAST_RESORT_CONFIRM` **and** both dual-gate probes
  passing:

  | Script | Meaning |
  | --- | --- |
  | `canada-last-resort-home-gate.sh` | exits 0 only when every `postgres-authority*` StatefulSet in Home's `pantry-bot` namespace reports zero ready replicas |
  | `canada-last-resort-oracle-gate.sh` | same check against Oracle's cluster |

  Both are private, host-level checks (SSH + `sudo kubectl`, run from an
  operator workstation — not a public route, and not a label on a pod: a
  stale `Terminating` pod was found live on 2026-09-21 still carrying an old
  `pantrybot.postgres/role=primary` label 12 hours after being fenced, so
  only the StatefulSet's own `readyReplicas` is trusted). **Unreachable is
  not proof of dark** — both gates fail closed if they cannot reach the
  target cluster at all, because a network partition could put a live writer
  on the far side of the same split that makes it unreachable from here.
- `publish-cloudflare-routes.sh` refuses a canada route publish without the
  same `CANADA_LAST_RESORT_CONFIRM` token; oracle/home route publishing
  stays token-free.
- No active old-writer fence runs during Canada's promotion: by the time
  both gates pass, Home and Oracle are already independently, positively
  proven dark, so there is no live writer left to race.
- Canada's PostgreSQL is a Docker Desktop container
  (`pantrybot-canada-postgres`), promoted with `SELECT pg_promote();` over
  `docker exec`, matching the pattern every other Canada script already
  uses. The old `podman-pantrybot-canada` WSL stack is broken and is being
  decommissioned; do not attempt recovery through Podman.
- CI gate: `tests/pantrybot-canada-last-resort-guard-test.sh` proves all of
  the above refusal paths without touching real SSH/Docker/Kubernetes.

## Where this runs

Unlike the old draft (which ran the promotion script on the Canada host
itself), this runs from an **operator workstation** with:

- SSH to the Canada host (`CANADA_ADMIN_KEY`, `BotAdmin@100.104.83.28`)
- SSH + passwordless `sudo kubectl` to Home's node (`minecraftmachine`)
- SSH + passwordless `sudo kubectl` to Oracle's node (`ubuntu@<oracle-tailscale-ip>`)

No new fencing credential needs to be installed on Canada itself — the
Home/Oracle dark-site checks read only (`kubectl get statefulset`), and the
existing per-cluster SSH access this project already uses nightly is
sufficient. This keeps Canada's own credential footprint limited to its
own local Docker/Postgres, which it already needs for `authority-gate.ps1`.

## Preflight

1. Confirm both preferred sites are dark:
   ```bash
   observability/failover-witness/canada-last-resort-home-gate.sh
   CANADA_LAST_RESORT_ORACLE_SSH_IDENTITY=~/.ssh/codex-maintenance-oracle \
     observability/failover-witness/canada-last-resort-oracle-gate.sh
   ```
2. Generate a one-time token:
   ```bash
   CANADA_LAST_RESORT_CONFIRM=$(date +%s%N | sha256sum | cut -c1-32)
   ```
   It is an acknowledgement of intent, not a secret bound to the recovery.
3. Dry-run first:
   ```bash
   CANADA_LAST_RESORT_CONFIRM="$CANADA_LAST_RESORT_CONFIRM" \
   CANADA_ADDRESS=100.104.83.28 \
   CANADA_ADMIN_KEY=~/.ssh/codex-maintenance-canada \
     observability/failover-witness/canada-last-resort-promote.sh --dry-run
   ```

## Promote Canada (one shot)

```bash
CANADA_LAST_RESORT_CONFIRM="$CANADA_LAST_RESORT_CONFIRM" \
CANADA_ADDRESS=100.104.83.28 \
CANADA_ADMIN_KEY=~/.ssh/codex-maintenance-canada \
  observability/failover-witness/canada-last-resort-promote.sh --confirm
```

This promotes the local standby (`pg_promote()`), waits for
`pg_is_in_recovery() = false`, runs `authority-gate.ps1 -Once` on Canada
(which starts any missing production application containers now that it
holds the lease and the database is primary — this reuses the exact logic
already live on the host rather than duplicating it), and verifies readiness
with `check-canada-ready.ps1`.

## Live routing to Canada (only if required)

Route publication to Canada is a separate, deliberate step and needs the
same token:

```bash
PANTRY_PROMOTION_SITE=canada \
CANADA_LAST_RESORT_CONFIRM="$CANADA_LAST_RESORT_CONFIRM" \
  observability/failover-witness/publish-cloudflare-routes.sh
```

## Restore normal operation

Restore runs always return routing and authority to home or Oracle — never
leave Canada holding authority longer than the recovery requires. Promote
the preferred site with the return-home / Oracle promotion sequences in
`pantrybot.md`, then verify: Canada's PostgreSQL is back in recovery, the
witness lease is back on the preferred site, and the restored site passes
its own readiness checks.

## Verification

- `git log` / the tracking issue records the one-shot promotion and the
  restore.
- The promoter fails closed for Canada unless the token AND both gates
  passed; this is enforced by
  `tests/pantrybot-canada-last-resort-guard-test.sh` in the repo gates.
- The dual gates were verified live on 2026-09-21 against the real
  production topology at the time (Home fenced, Oracle serving as primary):
  the Home gate correctly passed and the Oracle gate correctly refused,
  proving the fail-closed behavior against production state rather than a
  mock.

## Known remaining gap

Neither gate nor the promotion script has ever been exercised with **both**
Home and Oracle genuinely dark at once — that is a real, disruptive
production test requiring both sites intentionally fenced, and has not been
scheduled. Canada's production application containers also require
`C:\ProgramData\PantryBotCanadaPrep\canada-production.env`, which exists on
the live host but has not been reconciled into any secret-restoration
process in this repo (see `SECRET-RESTORATION-MATRIX.md`).

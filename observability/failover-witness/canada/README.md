# Canada live scripts (captured from production, 2026-09-21)

These files were pulled verbatim from
`C:\ProgramData\PantryBotCanadaPrep\` on the live Canada host
(Tailscale `100.104.83.28`, `truffles\botadmin`) because they implement real,
already-running production behavior that had never been committed to this
repo. Canada has been through real failover activity before; this directory
exists so that history stops living only on one Windows host.

Do not treat this as a design doc — it is a snapshot of what is actually
deployed. If you change one of these files here, deploy the same change to
the live host and re-verify (see `fence-canada-writer.ps1` in the parent
directory for the deploy/verify pattern: base64 over SSH, compare SHA256,
then `PSParser.Tokenize` for syntax).

## What each file does

- `authority-gate.ps1` — the live, continuously-running supervisor. Every 10
  seconds it renews (or acquires) the shared `pantry:postgres` witness lease
  as site `canada`. No lease, or local Postgres not in the primary role →
  stop every production application container immediately. Lease held and
  Postgres primary → start any missing production containers. This is
  Canada's half of the same witness protocol `oracle_promoter.py` uses; it
  does **not** call `pg_promote()` itself, only decides whether to serve.
- `start-canada-production.ps1` / `stop-canada-prod.ps1` — start/stop the
  seven production application containers via plain `docker run`
  (`pantrybot-canada-prod-api`, `-gateway`, `-worker`, `-dispatcher`,
  `-overlay`, `-private`, `-public`). **There is no docker-compose file
  involved** — a prior doc (`CANADA-ALWAYS-ON.md`) incorrectly referenced a
  `docker-compose.canada.yml` that does not exist; see that file's
  2026-09-21 correction.
- `health-monitor.ps1` — periodic JSONL health snapshot (database role,
  container status, readiness probes, witness reachability) appended to
  `health-monitor.jsonl`.
- `check-canada-ready.ps1` / `check-canada-ownership.ps1` /
  `check-canada-cutover-ready.ps1` — read-only probes against the loopback
  readiness/ownership ports of the running containers.

## Known gap

None of these scripts positively fence Home or Oracle before Canada could
serve as primary, and none perform the actual `pg_promote()` — promotion is
still a manual, out-of-band step. `fence-canada-writer.ps1` (parent
directory) is the fence Home/Oracle would call to stop Canada as an old
writer, not the other direction. The dual-gate last-resort promotion path
(`CANADA_LAST_RESORT_HOME_GATE` / `CANADA_LAST_RESORT_ORACLE_GATE`, one-shot
promotion) is designed but not yet built against this current architecture —
see the tracking issue for status.

# PantryBot ownership handoff rehearsal

This rehearsal proves the safety property required before active-active
production routing: only one site may own a fenced external-side-effect role,
and an old owner cannot continue after takeover.

## What it proves

1. The home owner acquires the configured resource at epoch 1.
2. The Oracle owner is rejected while the home lease is live.
3. Home releases the lease and Oracle acquires the next epoch.
4. The old home token is rejected by the database fence.
5. Oracle's current token remains valid until the rehearsal releases it.

## Run against an isolated PostgreSQL database

```sh
PANTRY_DATABASE_URL='postgres://...' \
PANTRY_REHEARSAL_RESOURCE='pantry:twitch-gateway' \
PANTRY_REHEARSAL_HOME_OWNER='home-gateway-rehearsal' \
PANTRY_REHEARSAL_ORACLE_OWNER='oracle-gateway-rehearsal' \
npm run rehearsal:ownership
```

The command initializes only the platform lease schema and prints a JSON
result. It does not connect to Twitch, change Kubernetes, change DNS, or
modify the legacy SQLite database. Use a disposable or explicitly isolated
database; do not point it at the production authority until the migration
runbook authorizes that step.

The automated pg-mem test is `test/ownershipRehearsal.test.ts`. The executable
rehearsal is `scripts/rehearse-ownership.ts`, backed by
`src/rehearsal/ownership.ts`.

`npm run rehearsal:failover` wraps the same ownership proof with UTC start/end
timestamps and explicit `routing_mutation=none` / `deployment_mutation=none`
markers. It is safe to run repeatedly against an isolated PostgreSQL database;
it does not change DNS, Kubernetes, Twitch, or overlay routing.

To populate the shared credential authority from the existing SQLite database,
use the one-time migration command with the SQLite path and the same encryption
key that will be provided to both sites:

```sh
PANTRY_SQLITE_PATH=/path/to/pantry.db \
PANTRY_DATABASE_URL='postgres://...' \
PANTRY_AUTH_ENCRYPTION_KEY='base64-32-byte-key' \
npm run migration:import-auth
```

It writes only encrypted token columns and prints role names, never token
values. Run it against an isolated PostgreSQL rehearsal first and verify the
gateway can read both roles before any production cutover.

## Gateway role boundary

The non-command Twitch gateway entrypoint is `npm run start:gateway`. It
requires the normal PantryBot config plus `PANTRY_DATABASE_URL`,
`PANTRY_SITE_ID`, `PANTRY_INSTANCE_ID`, and
`PANTRY_AUTH_ENCRYPTION_KEY`. It reads encrypted Twitch credentials from the
shared PostgreSQL `twitch_auth` table, refreshes them through that store, then
publishes only normalized durable events under the `pantry:twitch:ingress`
lease. It does not load the command registry or send replies. This entrypoint
is built and tested but has not been deployed to either site yet.

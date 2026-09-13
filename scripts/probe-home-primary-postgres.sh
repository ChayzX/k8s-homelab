#!/usr/bin/env bash
set -Eeuo pipefail

# Exit successfully only when the private home PostgreSQL endpoint is both
# writable and not in recovery. The transaction is rolled back so the probe
# cannot change application state.

command -v psql >/dev/null || {
  echo 'home-primary probe requires psql' >&2
  exit 127
}

: "${PGPASSFILE:?PGPASSFILE is required}"
: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=25432}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"

result=$(PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-3}" \
  psql -X -q -tA -w -v ON_ERROR_STOP=1 \
    -c 'BEGIN;
        CREATE TEMP TABLE home_primary_probe (ok boolean) ON COMMIT DROP;
        INSERT INTO home_primary_probe VALUES (true);
        ROLLBACK;
        SELECT CASE
          WHEN pg_is_in_recovery() THEN $$standby$$
          WHEN current_setting($$transaction_read_only$$) <> $$off$$ THEN $$readonly$$
          ELSE $$primary$$
        END;' 2>/dev/null)

[[ "$result" == "primary" ]] || {
  echo "home-primary probe failed: ${result:-no result}" >&2
  exit 1
}

echo home_primary=confirmed

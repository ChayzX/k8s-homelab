#!/bin/sh
# Reseed Canada's Postgres volume as a streaming standby of a remote primary
# (#191). Runs inside a one-off postgres container with the data volume at
# $PGDATA; the replication password arrives on stdin (never argv/env/logs).
# Usage (from Canada): docker run --rm -i -v pantrybot-canada-postgres-data:/var/lib/postgresql/data
#   -v <this file>:/reseed.sh:ro -e PRIMARY_HOST=.. -e PRIMARY_PORT=.. -e PRIMARY_SLOT_NAME=..
#   --entrypoint sh postgres:rehearsal /reseed.sh < password
set -eu
D=/var/lib/postgresql/data
: "${PRIMARY_HOST:?}" "${PRIMARY_PORT:?}" "${PRIMARY_SLOT_NAME:?}"
USER_NAME="${REPLICATION_USER:-pantry_replicator}"
APP_NAME="${APP_NAME:-pantry-canada-standby}"
IFS= read -r PW || true
PW=$(printf %s "$PW" | tr -d '\r\n')
[ -n "$PW" ] || { echo "reseed=failed reason=no_password" >&2; exit 1; }
umask 077
printf '%s:%s:*:%s:%s\n' "$PRIMARY_HOST" "$PRIMARY_PORT" "$USER_NAME" "$PW" > /tmp/pgpass
chown postgres:postgres /tmp/pgpass
PGPASSFILE=/tmp/pgpass gosu postgres pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$USER_NAME"
find "$D" -mindepth 1 -delete
chown postgres:postgres "$D"; chmod 700 "$D"
PGPASSFILE=/tmp/pgpass gosu postgres pg_basebackup -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$USER_NAME" \
  -D "$D" -Fp -X stream --slot="$PRIMARY_SLOT_NAME" --checkpoint=fast
install -o postgres -g postgres -m 600 /tmp/pgpass "$D/.pgpass"
gosu postgres touch "$D/standby.signal"
sed -i '/^primary_conninfo[[:space:]]*=/d; /^primary_slot_name[[:space:]]*=/d; /^default_transaction_read_only[[:space:]]*=/d' "$D/postgresql.auto.conf"
printf "primary_conninfo = 'user=%s passfile=%s/.pgpass host=%s port=%s application_name=%s'\n" \
  "$USER_NAME" "$D" "$PRIMARY_HOST" "$PRIMARY_PORT" "$APP_NAME" >> "$D/postgresql.auto.conf"
printf "primary_slot_name = '%s'\n" "$PRIMARY_SLOT_NAME" >> "$D/postgresql.auto.conf"
test -f "$D/standby.signal" && test -f "$D/PG_VERSION" && echo RESEED_COMPLETE

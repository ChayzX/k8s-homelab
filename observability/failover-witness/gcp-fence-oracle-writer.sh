#!/usr/bin/env sh
set -eu

# GCP witness-side Oracle fence adapter. The reverse tunnel is loopback-only;
# the Oracle authorized key is forced to the exact destructive fence command.
exec /usr/bin/ssh \
  -i /etc/failover-witness/oracle-fence \
  -o BatchMode=yes \
  -o ConnectTimeout=5 \
  -o StrictHostKeyChecking=yes \
  -p 18767 \
  ubuntu@127.0.0.1 \
  fence-pantry-postgres-oracle --confirm

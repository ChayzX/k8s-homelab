#!/usr/bin/env sh
set -eu

# Installed root-owned on the GCP witness. The private key never leaves GCP;
# the reverse SSH destination is ChaseBot's forced-command path.
exec /usr/bin/ssh \
  -i /etc/failover-witness/home-fence \
  -o BatchMode=yes \
  -o ConnectTimeout=5 \
  -o StrictHostKeyChecking=yes \
  -p 2223 \
  root@127.0.0.1 \
  fence-auth-postgres --confirm

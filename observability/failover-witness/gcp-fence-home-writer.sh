#!/bin/sh
set -eu

# Installed root-owned on the GCP witness. The Oracle promoter invokes this
# through the existing OS Login SSH connection. The private key never leaves
# GCP, and the home key is a forced-command key with no shell access.
exec /usr/bin/ssh \
  -i /etc/failover-witness/home-fence \
  -o BatchMode=yes \
  -o ConnectTimeout=5 \
  -o StrictHostKeyChecking=yes \
  -p 2223 \
  root@127.0.0.1 \
  fence-pantry-postgres

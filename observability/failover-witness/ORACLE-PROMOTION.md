# Oracle PostgreSQL promotion controller

`oracle_promoter.py` is the target-side companion to the home/Oracle
software self-fence. It uses one shared witness resource, `pantry:postgres`:

1. normal operation: home holds the lease and Oracle waits;
2. home loss or self-fence: the home lease expires;
3. Oracle acquires the same lease as holder `oracle`;
4. the local standby is promoted and verified writable;
5. the local PostgreSQL Service and PantryBot database URL are switched to the
   Oracle primary;
6. API/private/overlay capacity is restarted and verified;
7. gateway, worker, and dispatcher deployments are scaled from zero and
   verified; and
8. renewal loss stops Oracle k3s, fencing the promoted writer domain.

The controller does not promote while home still holds the shared lease. The
home self-fence must be installed and tested first; a witness lease alone is
not physical STONITH. The promotion controller therefore remains part of the
software-fencing evidence, with the physical-fence limitation recorded in
GitHub Issues #147 and #191.

Normal-mode Oracle application capacity uses the encrypted Oracle-local
forward to the home primary. The Oracle standby is intentionally read-only
until this controller performs promotion. The endpoint switch is local to the
Oracle cluster and does not require paid Cloudflare load balancing.

Reverse transport for return-home recovery is separate and always read-only
until a failback procedure uses it: Oracle publishes local PostgreSQL through
GCP loopback port `25433`, and ChaseBot exposes that path at
`192.168.40.200:25433`. The reverse tunnel carries no application traffic in
normal mode and does not by itself reconfigure the home StatefulSet.

The controlled return procedure is documented in
`docs/recovery/PANTRYBOT-POSTGRES-FAILBACK.md`. It seeds a new home PVC with
`docs/recovery/pantrybot-postgres-standby-home-failback.yaml`, fences Oracle,
then performs the witness-epoch handoff before switching the stable Service.
Automatic failback remains disabled until that procedure has one complete
measured rehearsal.

The disposable `pantrybot-postgres-failback-rehearsal.yaml` uses only
`emptyDir`. Before applying it, copy the existing replication Secret into the
rehearsal namespace with sanitized metadata; never commit that Secret. The
2026-09-11 rehearsal completed `pg_basebackup -R` over the reverse path and
verified `standby.signal` plus `postgresql.auto.conf`, then deleted the entire
namespace.

Verification:

```sh
sudo /usr/local/lib/failover-witness/oracle-promotion-preflight.sh
python3 observability/failover-witness/test_oracle_promoter.py
python3 -m py_compile observability/failover-witness/oracle_promoter.py
```

The preflight is read-only and must report `recovery=true`, `read_only=on`,
non-empty receive/replay LSNs, the live container data directory, and an
inactive promoter before an operator begins a controlled promotion.

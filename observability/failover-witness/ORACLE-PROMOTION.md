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

Verification:

```sh
python3 observability/failover-witness/test_oracle_promoter.py
python3 -m py_compile observability/failover-witness/oracle_promoter.py
```

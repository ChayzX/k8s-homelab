# Operations Oracle writable-recovery contract

This is the homelab-side operator contract for Issue #201. The Operations
application contract is maintained in `Operations-ios-app` (PR #163); this
document binds that contract to the home/Oracle recovery controls maintained
here.

This procedure is deliberately manual until the evidence gates are complete.
It must be run only during an approved maintenance window, against a named
Oracle Kubernetes context, and with an evidence file that contains no secret
values. It is not a command to run unattended and it does not authorize
active-active SQLite writers.

## Promotion order

Do not skip or reorder these phases. If a phase cannot produce the required
evidence, stop with Oracle read/API capacity only.

1. **Name and verify the target.** Set explicit `HOME_CONTEXT` and
   `ORACLE_CONTEXT` values. Confirm each context resolves to the expected
   cluster/node with `scripts/assert-kube-target.sh`; never rely on the current
   kubectl context or a DNS name as a safety boundary.
2. **Select and verify the restore artifact.** Record the R2 object, generation,
   byte size, SHA-256, image digest, architecture, and expected table count.
   Decompress into an isolated local path and run SQLite `PRAGMA
   integrity_check`; do not overwrite the source or an existing production
   PVC.
3. **Prepare Oracle without writes.** Apply/render the Operations Oracle
   recovery overlay from `Operations-ios-app`. Keep the deployment at zero or
   in its checked-in `mutationMode=disabled` read/API form until promotion is
   explicitly authorized. Verify the restored PVC and `/livez`/`/readyz` from
   the restored copy, not merely from the image.
4. **Prove emergency access before the outage test.** With Authentik and LDAP
   unavailable in the isolated recovery target (not production), connect over
   the pre-provisioned owner recovery path, such as host SSH plus a loopback
   port-forward. Prove an authorized dashboard read and a harmless audited
   synthetic mutation using the documented recovery authentication mechanism.
   Record only mechanism name, target boundary, sanitized status, and cleanup;
   never record credentials. A health endpoint, an Authentik session, or an
   unsigned identity header is not emergency-access evidence.
5. **Fence the home writer.** Use the independently authorized home-writer
   fence adapter and capture its result. Scaling a Deployment to zero,
   changing a route, or losing a tunnel is not a fence. Verify the home
   Operations process/PVC cannot accept a write and that no home writer
   endpoint remains before continuing.
6. **Enable one Oracle writer.** Restore the selected generation to the
   Oracle-local RWO PVC, re-run integrity/readiness checks, enable the reviewed
   writable Operations configuration, and scale exactly one Oracle writer.
   Verify one synthetic audit mutation and its persisted result. If the
   application cannot reject a stale home write, stop and do not proceed.
7. **Change public routing last.** After the writer and emergency-access gates
   pass, update the external route to Oracle and verify it from outside both
   clusters. Record detection, fence, promotion, route convergence, RTO, RPO,
   and duplicate-write/ordering results.

## Return-home order

Keep Oracle as the writer until home has been re-seeded and independently
verified. Capture the final Oracle backup, stop and verify the Oracle writer,
fence Oracle's writer domain, restore the selected generation to home, verify
integrity and emergency access, then enable exactly one home writer. Change the
public route only after home write/readiness checks pass. Record the new epoch
and rollback result. Never acquire home ownership before Oracle fencing is
confirmed.

## Explicit stop conditions

- Do not promote an empty or unverified PVC.
- Do not treat the read-only Oracle overlay as writable by editing a live pod.
- Do not use Cloudflare/DNS, a tunnel, or a failed health check as database
  fencing.
- Do not run two SQLite writers or share `operations.db` over the WAN.
- Do not bypass Authentik by trusting an unsigned proxy header.
- If any proof is missing, preserve the artifact and leave Oracle read-only.

The current evidence passes isolated restore/readiness and Oracle read/API
self-healing only. Writable restore, Authentik-independent authorized
mutation, home-writer fencing, route convergence, measured RTO/RPO, duplicate
behavior, and rollback remain open until recorded on Issue #201.

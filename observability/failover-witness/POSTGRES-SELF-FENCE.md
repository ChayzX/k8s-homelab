# PostgreSQL software self-fence

`fence_agent.py` is a fail-closed, witness-backed self-fence for a
site-local PostgreSQL writer. It is deliberately separate from the ordinary
PantryBot application leases:

- resource: `pantry:postgres:home` or `pantry:postgres:oracle`;
- the writer acquires a short-lived witness epoch before its site starts;
- every renewal failure immediately runs the configured local fence command;
- a failed fence command is fatal, so systemd retries instead of continuing;
- the agent must start before the local k3s writer domain.

This is software self-fencing, not physical STONITH. It protects against a
healthy host losing witness connectivity and against stale database startup
after reboot. It does not protect against a compromised or frozen host, so it
must remain part of the explicit promotion evidence and must not be described
as a universal partition fence until a failure test proves the old writer
rejects a commit.

## Installation shape

1. Copy `fence_agent.py` to `/usr/local/lib/failover-witness/` as root.
2. Create `/etc/failover-witness/postgres-fence.env` mode `0600`, owned by
   root, with only `WITNESS_URL` and `WITNESS_SHARED_SECRET`.
3. Copy the example unit to the site-specific systemd unit and change the
   site, resource, tunnel dependency, and local fence command. Home ChaseBot
   should stop `k3s-agent.service`; Oracle should stop `k3s.service`.
4. Install the matching `k3s-writer-fence-drop-in.conf.example` as a drop-in
   for that service. `Requires` and `After` are required: `Before` on the
   fence unit alone does not prevent k3s from starting after a failed acquire.
5. Install the unit but do not enable it until the site-specific fence command
   has been tested in a maintenance window. The command must stop the entire
   local Kubernetes writer domain, not merely delete a pod.
6. Validate startup ordering, witness acquisition, renewal loss, and fence
   completion before enabling any automatic PostgreSQL promotion.

The agent intentionally has no automatic restart path for the database. After
a self-fence, the operator or a promotion controller must reacquire authority
and explicitly start the writer domain. This prevents a stale site from
returning as a writer after its witness lease expires.

Verification:

```sh
python3 observability/failover-witness/test_fence_agent.py
python3 -m py_compile observability/failover-witness/fence_agent.py
```

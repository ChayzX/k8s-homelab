# Opsbot ownership/fencing rehearsal

This slice is locally testable without credentials, but production failover
is not proven until the following external gate is completed with the real
Discord token, witness endpoint, and both site deployments.

## Exact gate

1. Provision `opsbot-witness` in both clusters with the same
   `OPSBOT_WITNESS_SECRET`, set `OPSBOT_SITE=home` and `OPSBOT_SITE=oracle`
   respectively, and set each `OPSBOT_WITNESS_URL` to the same reachable
   neutral witness. Do not proceed if either pod cannot reach `/healthz`.
2. Start only the home Opsbot. Verify logs contain `site lease acquired` and
   verify the Discord gateway is connected. Verify the home pod can perform a
   harmless authorized status command and, in a controlled window, one
   approved restart against a disposable/non-production Deployment.
3. Start the Oracle Opsbot while home's lease is valid. It must exit before
   opening a Discord gateway; witness acquire must return 409 and the Oracle
   logs must contain the fatal no-lease gate.
4. Stop or isolate the home witness-renewal path and wait longer than the
   configured 30-second lease. The home process must withdraw `/health` and
   `/healthz` readiness before closing Discord, then refuse a subsequent
   deployment/RCON operation; record the fencing timestamp. A 503 readiness
   response is required evidence that the old owner is no longer routable.
5. Start Oracle. It must acquire a higher epoch, connect to Discord, and pass
   the same disposable mutation check. Restore home connectivity but do not
   start it until the witness rejects its old epoch/token; then verify the
   stale home token cannot renew.

The gate is currently blocked until the `opsbot-witness` Secret is provisioned
in both environments and a reachable neutral witness is available. Local
evidence is limited to `test_ownership.py` and `test_k8s_ownership.py`.

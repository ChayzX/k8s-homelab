# Independent home-writer fence transport

The Authentik and PantryBot promoters need an out-of-band operation that can
stop the home PostgreSQL writer after the witness grants a new epoch. A witness
lease alone cannot fence a host during a network partition.

The free transport uses the already-independent GCP witness as a relay:

1. ChaseBot maintains a loopback-only reverse SSH forward on GCP port `2223`.
2. GCP holds a dedicated root-owned `home-fence` key.
3. ChaseBot's root `authorized_keys` entry is a forced command that accepts
   only `fence-pantry-postgres --confirm`. It stops the Home
   `pantry:postgres` lease renewer, verifies that it is inactive, and then uses
   a least-privilege Kubernetes identity to remove only the named PantryBot
   PostgreSQL workloads. A scoped-fence failure hard-fences ChaseBot's
   `k3s-agent` writer domain and still returns failure to prevent promotion.
4. Oracle invokes the GCP wrapper through its existing OS Login SSH path.

The key has no shell, agent forwarding, X11 forwarding, or port forwarding.
The transport is installed and connectivity-tested separately from a live
fence rehearsal. Do not invoke the forced command during normal operation;
the rehearsal must explicitly verify that the PostgreSQL writer stops, stale
home application writers cannot commit, and Oracle promotion then converges.

The Oracle Authentik promoter should use:

```text
ssh -i /home/ubuntu/.ssh/gcp-witness-oracle \
  sa_105559435168833655240@136.113.178.106 \
  sudo /usr/local/lib/failover-witness/gcp-fence-home-writer.sh
```

The exact command belongs in the root-owned Oracle environment/service
configuration, not in Git. The same wrapper can be used by the PantryBot
promoter after its maintenance-window rehearsal passes.

## ChaseBot least-privilege credential

Apply `pantry-postgres-fencer-rbac.yaml` from the Home control plane. Its
`kubernetes.io/service-account-token` Secret is deliberate: a bound token from
`kubectl create token` expires and can silently disable an unattended fence.
The long-lived token remains revocable by deleting the Secret and is restricted
to get/patch/update/delete on the three named Home PostgreSQL StatefulSets,
pods, services, and endpoints. No secret value belongs in Git.

Build the ChaseBot kubeconfig in a root-only temporary file on the control
plane using the Secret's `token` and `ca.crt`, the fixed Home API endpoint, and
context namespace `pantry-bot`. Transfer it through the established
administrative SSH path and install it as:

```text
/etc/failover-witness/pantry-postgres-fencer.kubeconfig
owner root:root
mode 0600
```

Do not print the token or embed it in an issue, log, shell history, or command
line. Validate the installed identity with non-mutating named `get` operations
for each permitted object and a denied read outside `pantry-bot`. Then run the
Oracle Home-fence dry-run and the GCP reverse-SSH rejection probe before any
live fence rehearsal.

Rotation is create replacement Secret, build/install a new kubeconfig, prove
the non-mutating access checks, then delete the old Secret. Rollback restores
the previous root-only kubeconfig only while its backing Secret remains valid.

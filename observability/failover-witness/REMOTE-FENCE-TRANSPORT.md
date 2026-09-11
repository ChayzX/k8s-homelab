# Independent home-writer fence transport

The Authentik and PantryBot promoters need an out-of-band operation that can
stop the home PostgreSQL writer after the witness grants a new epoch. A witness
lease alone cannot fence a host during a network partition.

The free transport uses the already-independent GCP witness as a relay:

1. ChaseBot maintains a loopback-only reverse SSH forward on GCP port `2223`.
2. GCP holds a dedicated root-owned `home-fence` key.
3. ChaseBot's root `authorized_keys` entry is a forced command that accepts
   only `fence-pantry-postgres` and runs the installed writer-domain helper.
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

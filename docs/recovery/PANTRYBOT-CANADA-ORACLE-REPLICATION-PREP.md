# Canada to Oracle replication preparation

Workspace preparation, 2026-09-14. Nothing in this bundle has been deployed.
Canada PostgreSQL currently binds on the Windows host only at
**`127.0.0.1:15432`**. Connecting to `100.104.83.28:15432` from Oracle will not
reach that listener. Preserve this loopback binding; do not expose PostgreSQL
on the laptop's LAN or Tailscale interfaces to establish replication.

## Transport and persistent runtime

```text
Oracle PostgreSQL standby (hostNetwork, local port 25443)
    -> Oracle 127.0.0.1:25442 (sshd remote-forward listener)
    -> encrypted SSH connection maintained by Canada SYSTEM scheduled task
    -> Canada Windows 127.0.0.1:15432
    -> current PantryBot PostgreSQL container, port 5432
```

Ports 25442/25443 are proposed new ports. Verify they are unused; existing
port 25432 may belong to older home replication and must not be taken over.
The SSH task runs independently of the BotAdmin interactive session and has
startup/reconnect behavior. Its only forwarded destination is the existing
local PostgreSQL listener. It does not start, stop or replace PantryBot.

Windows artifact:
`outputs/canada-pantrybot-prep/start-oracle-replication-tunnel.ps1` in the review
workspace. Install at
`C:\ProgramData\PantryBotCanadaPrep\start-oracle-replication-tunnel.ps1`.
The script's `-InstallTask` mode registers a SYSTEM startup task without
starting it. Installation requires the dedicated private key and pinned Oracle
host key. The installed task retries every five seconds, checks the SSH server
every ten seconds, runs while on battery, and permits only one task instance.
Monitor/rotate its log at `C:\ProgramData\PantryBotCanadaPrep\replication-tunnel.log`.

The key directory is
`C:\ProgramData\PantryBotCanadaPrep\replication-ssh`. Restrict its private key
to SYSTEM and the designated administrative account; do not store the key in
Git, scripts or task arguments. Populate `known_hosts` using Oracle's host key
fingerprint verified over the existing trusted administrative connection.
Do not disable host-key checking or accept a fingerprint only because it was
returned by the new network connection.

Proposed dedicated Oracle account: `pantry-replication-relay`. Its authorized
key entry contains the public key only:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:25442",command="/bin/false" ssh-ed25519 REPLACE_WITH_DEDICATED_PUBLIC_KEY
```

Use an account-specific sshd configuration, validate with `sshd -t`, and
review effective settings before any reload:

```text
Match User pantry-replication-relay
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:25442
    GatewayPorts no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitUserRC no
    MaxSessions 0
```

`ssh -NT` requests forwarding without a shell. Verify the actual key can create
the permitted loopback listener and cannot open a shell or other forwards.
The SSH task must not use an unrestricted general administrator credential.
An SSH process running successfully does not prove that the database behind
the forward is reachable; validate with a read-only PostgreSQL query.

## Separate standby preparation

`pantrybot-canada-standby-prep.yaml` creates a **separate namespace and new PVC**
with **zero replicas**. It does not change the existing Oracle database,
application services, witness lease, or Cloudflare records. The prepared
database listens on Oracle loopback port 25443. `hostNetwork: true` is needed
to access the loopback reverse tunnel; use the explicit verified Oracle node
label `pantrybot.site=oracle`. This is not an application endpoint yet.

The image is the pinned PostgreSQL 16 Alpine image recorded for Canada.
Confirm Canada's actual major version, extension binaries, locale/collation
support and disk use before starting the copy. Allocate sufficient PVC and
node disk headroom; the example requests 16 GiB and must be sized from evidence.

Create Secret `canada-replication-passfile` in the preparation namespace out of
band. It must contain key `pgpass` holding a valid libpq passfile entry for
`127.0.0.1:25442:*:replicator:<password>`. Escape colons and backslashes using
libpq rules. Do not commit the value. The init container copies the passfile
to a memory-backed volume with mode 0600 and PostgreSQL ownership; it never
puts the password into the recovery configuration or process arguments.

The source needs a reviewed least-privilege replication login, matching HBA
rule, `wal_level` of replica/logical, sufficient WAL sender/slot capacity, and
an unused physical slot named `oracle_from_canada`. Determine the source
address seen inside the Canada container for this loopback Docker forward;
do not assume it is 127.0.0.1 inside PostgreSQL's network namespace.

Creating a replication role, HBA rule or slot is a production change and is
not performed by this bundle. Inspect existing settings first; some WAL
settings require a PostgreSQL restart. Do not restart Canada during streaming.
Set a reviewed WAL-retention ceiling and monitor free disk: a disconnected
standby slot can otherwise fill the active laptop's disk. Slot invalidation
must block promotion and trigger a controlled re-seed.

The init container uses `pg_basebackup -R --wal-method=stream` with that slot,
spread checkpointing and an 8 MiB/s base-copy limit. WAL streaming and source
checkpoint I/O are not fully bounded by that copy limit; observe source latency,
CPU, network and disk throughout preparation. Basebackup runs only against an
empty target directory. Partial copies are retained for inspection and are
never silently erased. On restart, an existing copy is used only if its
bootstrap marker, PG16 version, and `standby.signal` all exist. Promotion removes
`standby.signal`, so this preparation manifest will not silently restart an
unreviewed primary.

## Read-only freshness validation

Run `check-canada-replica-freshness.py` on Oracle with a reviewed copy of
`canada-replica-freshness.example.json`. It invokes two trusted psql argument
arrays, sends a read-only SQL transaction on stdin, and prints only sanitized
replication metadata. The source probe connects through 127.0.0.1:25442; the
standby probe uses `kubectl exec` and the local PostgreSQL socket on port 25443.
Use protected passfiles for authentication. The probe needs permission for
the PostgreSQL control/replication information functions; do not add blanket
superuser credentials merely for monitoring.

Supply the **verified current Canada system identifier** and explicit byte/
sample-age limits. Example with a zero observed WAL-backlog gate:

```text
python3 check-canada-replica-freshness.py --config /etc/pantrybot/canada-replica-freshness.json --expected-system-identifier VERIFIED_CANADA_SYSTEM_IDENTIFIER --expected-slot oracle_from_canada --max-lag-bytes 0 --max-sample-age-seconds 30
```

The check verifies primary/standby roles, database identity, major version,
source/receiver/replay timeline, streaming slot, recent samples, and received/
replayed WAL against the sampled primary flush position. An idle database is
not marked stale merely because its last transaction was old. A changed
timeline, missing WAL or unreachable source fails the check.

Passing is **live replication evidence, not promotion authority**. The script
always prints `promotion_authorized: false`. It does not prove zero data loss
after the source disappears, satisfy fencing, verify application schema, or
replace a post-fence final-WAL/approved-RPO decision. Promotion still requires
the shared witness lease, positive old-writer fencing, target replay completion,
and an explicit acceptable loss policy.

## Next safe steps and remaining live evidence

1. Read-only: verify Canada's current image/version, system identifier, WAL
   settings, replication roles/HBA, slots, database size and free disk. Verify
   Oracle machine identity, available ports, node label and storage headroom.
2. Stage the dedicated key, server restrictions and Windows task; test only
   the tunnel with read-only database queries. Preserve production ports.
3. After any required source configuration is reviewed, start the separate
   standby with a monitored copy. Do not replace or promote the existing DB.
4. Collect repeated freshness observations, disconnect/reconnect the replication
   tunnel without affecting Canada clients, and verify streaming resumes.
5. Resolve positive database/sender fencing and authority-aware routing before
   integrating this standby with the production promoter. The candidate's
   namespace, pod, port and service selectors differ from existing production
   defaults; selecting it is a separate reviewed change.

Documentation references: [PostgreSQL 16 basebackup](https://www.postgresql.org/docs/16/app-pgbasebackup.html)
and [PostgreSQL replication monitoring](https://www.postgresql.org/docs/16/monitoring-stats.html).

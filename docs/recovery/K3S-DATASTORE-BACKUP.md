# k3s Datastore and Server-Token Recovery Gate

This procedure is intentionally not executed by an unprivileged project
worker. The k3s server directory is root-only and the datastore may contain
Kubernetes Secret data. Do not copy the database or server token into Git,
shell output, an issue comment, or an unencrypted backup.

## Current evidence

- `minecraftmachine` is the only k3s server.
- The k3s service has no datastore flags and its journal uses `kine.sock`,
  strongly indicating the default SQLite/Kine datastore.
- The expected SQLite path is
  `/var/lib/rancher/k3s/server/db/state.db`; the matching server token is
  `/var/lib/rancher/k3s/server/token`.
- Both paths were verified with root access on 2026-09-10; SQLite
  `pragma quick_check` returned `ok`. The first encrypted artifact is
  `k3s-20260910T023902Z.tar.gz.gpg`, mode 600, with SHA-256
  `33f062100d31cf3a233984480a0b3197d371ac55940505ae899477d0329ead74`.
  Its R2 copy under `recovery/k3s-control-plane/` was downloaded as a stream
  and matched the local checksum. The non-destructive restore check passed on
  2026-09-09: decryption, archive members, SQLite `quick_check`, and Kine-row
  presence were verified without replacing the live datastore.

## Controlled procedure

Run on `minecraftmachine` during a maintenance window, as root or through a
root-approved wrapper. First confirm the paths and backend without printing
file contents:

```sh
sudo test -s /var/lib/rancher/k3s/server/db/state.db
sudo test -s /var/lib/rancher/k3s/server/token
sudo sqlite3 /var/lib/rancher/k3s/server/db/state.db 'pragma quick_check;'
```

Create the backup with the repository script. It uses SQLite's online backup
operation, prompts for a passphrase through the operator's terminal, encrypts
the package with AES-256 GPG symmetric encryption, verifies the encrypted
archive by decrypting it into a stream, and removes all plaintext staging files:

```sh
sudo scripts/k3s-control-plane-backup.sh
```

Write the passphrase on paper and keep it separately from the host. The
encrypted artifact and R2 copy are checksum-verified, and both the
non-destructive artifact check and full disposable K3s restore have passed. Any
future restore must start from an isolated destination with the matching
server token and must not replace the live database or change the server's
role. Record only the artifact name, size, checksum, and restore result in
Issue #191. Symmetric encryption means loss of the passphrase makes the backup
unrecoverable; do not put the passphrase in GitHub, the repository, shell
history, or chat.

Run the non-destructive artifact rehearsal before attempting an isolated k3s
server restore:

```sh
sudo scripts/k3s-control-plane-restore-check.sh
```

It verifies decryption, archive contents, SQLite integrity, and Kine rows
without replacing the live database. This check passed on 2026-09-09. The
stronger disposable server rehearsal passed on 2026-09-10; never replace the
live database or change the server's role as part of a future rehearsal.

The repository also includes a disposable Docker-based isolated server
rehearsal. It uses the matching published K3s image, a temporary root-owned
data directory, a disposable user-defined Docker bridge network, and localhost port `16443`; it
does not mount `/var/lib/rancher/k3s` and removes the container, network, and
plaintext staging data on exit:

```sh
sudo scripts/k3s-control-plane-isolated-restore.sh --timeout 120
```

Enter the paper-held passphrase at the local terminal. Success is reported as
`isolated_restore=passed`. If the API does not start or expected namespaces
are absent, the script prints the temporary container log before cleanup.
The rehearsal passed on 2026-09-10 against the retained encrypted artifact;
the live k3s service and datastore remained untouched.

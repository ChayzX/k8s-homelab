#!/usr/bin/env bash
set -Eeuo pipefail

# Bring a fenced k3s site's PostgreSQL back as a standby of the current
# primary (#191). Run from a systemd timer on Home and Oracle.
#
# Acts ONLY when the site's DB StatefulSet is at 0 and every writer
# Deployment is at 0 (a fenced, non-serving site):
# - PGDATA has standby.signal (a fenced standby, or a primary that handed
#   back): scale to 1 ONLY once a unique primary exists elsewhere. Starting it
#   earlier lets two standbys cascade from each other so nobody promotes.
#   If this site handed back (HANDBACK_MARKER) and no primary appeared within
#   RECLAIM_AFTER seconds, start it with primary_conninfo cleared so its own
#   promoter can reclaim authority through the normal gate.
# - No standby.signal (an old primary fenced by a failover): keep a backup,
#   then reseed from the UNIQUE reachable primary with the same system
#   identifier. Discovery runs inside the reseed pod BEFORE any file is
#   deleted; none/ambiguous => the pod exits and the data is untouched.
: "${REJOIN_SITE:?}" "${REJOIN_STATEFULSET:?}" "${REJOIN_PVC:?}" "${REJOIN_NODE:?}" "${REJOIN_PEERS:?}" "${EXPECTED_SYSTEM_IDENTIFIER:?}"
NS="${PANTRY_NAMESPACE:-pantry-bot}"
WRITERS="${REJOIN_WRITER_DEPLOYMENTS:-pantry-private-api pantry-overlay-delivery pantry-twitch-gateway pantry-twitch-dispatcher pantry-chat-worker pantry-private-site app-cloudflared}"
IMAGE="${REJOIN_IMAGE:-postgres:16-alpine}"
SECRET="${REJOIN_REPLICATION_SECRET:-canada-replication-source}"
SLOT="pantry_${REJOIN_SITE}_standby"
POD="pantry-rejoin-${REJOIN_SITE}"
MARKER="${HANDBACK_MARKER:-/var/lib/pantry-postgres-promoter/handback.json}"
RECLAIM_AFTER="${RECLAIM_AFTER:-180}"
k() { kubectl -n "$NS" "$@"; }
say() { echo "standby_rejoin site=$REJOIN_SITE $*"; }

replicas="$(k get statefulset "$REJOIN_STATEFULSET" -o jsonpath='{.spec.replicas}')"
[[ "$replicas" == 0 ]] || { say "action=none reason=db_running"; exit 0; }
read -r -a writers <<<"$WRITERS"
for d in "${writers[@]}"; do
  r="$(k get deployment "$d" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)"
  [[ "${r:-0}" == 0 ]] || { say "action=none reason=writer_running:$d"; exit 0; }
done
pv="$(k get pvc "$REJOIN_PVC" -o jsonpath='{.spec.volumeName}')"
path="$(kubectl get pv "$pv" -o jsonpath='{.spec.local.path}{.spec.hostPath.path}')"
[[ -d "$path" ]] || { say "action=none reason=pv_path_missing"; exit 1; }

MODE=reseed
if [[ -f "$path/standby.signal" ]]; then MODE=probe; fi

if [[ "$MODE" == reseed ]]; then
install -d -m 700 /var/backups/pantry
backup="/var/backups/pantry/${REJOIN_STATEFULSET}-pre-rejoin-$(date -u +%Y%m%dT%H%M%SZ).tgz"
tar -C "$path" -czf "$backup" . && chmod 600 "$backup"
say "action=reseeding backup=$backup"
fi

k delete pod "$POD" --ignore-not-found --wait=true >/dev/null
k apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: {name: $POD, namespace: $NS, labels: {app.kubernetes.io/name: pantry-standby-rejoin}}
spec:
  restartPolicy: Never
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet
  nodeName: $REJOIN_NODE
  securityContext: {seccompProfile: {type: RuntimeDefault}}
  containers:
  - name: reseed
    image: $IMAGE
    env:
    - {name: REPL_PASSWORD, valueFrom: {secretKeyRef: {name: $SECRET, key: password}}}
    - {name: PEERS, value: "$REJOIN_PEERS"}
    - {name: EXPECTED_SYSID, value: "$EXPECTED_SYSTEM_IDENTIFIER"}
    - {name: SLOT, value: "$SLOT"}
    - {name: APP_NAME, value: "pantry-${REJOIN_SITE}-standby"}
    - {name: MODE, value: "$MODE"}
    command: ["sh", "-ec"]
    args:
    - |
      D=/var/lib/postgresql/data
      umask 077
      printf '*:*:*:pantry_replicator:%s\n' "\$(printf %s "\$REPL_PASSWORD" | tr -d '\r\n')" > /tmp/pgpass
      chown postgres:postgres /tmp/pgpass
      found=""; n=0
      for e in \$PEERS; do
        hp=\${e#*=}; h=\${hp%:*}; p=\${hp##*:}
        out=\$(PGPASSFILE=/tmp/pgpass psql -X -At -d "host=\$h port=\$p user=pantry_replicator dbname=pantry replication=database connect_timeout=5" -c "select pg_is_in_recovery(), (select system_identifier from pg_control_system())" 2>/dev/null) || continue
        [ "\${out%%|*}" = f ] && [ "\${out##*|}" = "\$EXPECTED_SYSID" ] && { found=\$hp; n=\$((n+1)); }
      done
      [ "\$n" -eq 1 ] || { echo "REJOIN_ABORT primaries=\$n (data untouched)"; exit 3; }
      H=\${found%:*}; P=\${found##*:}
      if [ "\$MODE" = probe ]; then echo "REJOIN_PRIMARY \$found"; exit 0; fi
      PGPASSFILE=/tmp/pgpass psql -X -d "host=\$H port=\$P user=pantry_replicator replication=true" -c "CREATE_REPLICATION_SLOT \$SLOT PHYSICAL RESERVE_WAL" >/dev/null 2>&1 || true
      find "\$D" -mindepth 1 -delete
      chown postgres:postgres "\$D"; chmod 700 "\$D"
      PGPASSFILE=/tmp/pgpass gosu postgres pg_basebackup -h "\$H" -p "\$P" -U pantry_replicator -D "\$D" -Fp -X stream --slot="\$SLOT" --checkpoint=fast
      install -o postgres -g postgres -m 600 /tmp/pgpass "\$D/.pgpass"
      gosu postgres touch "\$D/standby.signal"
      sed -i '/^primary_conninfo[[:space:]]*=/d; /^primary_slot_name[[:space:]]*=/d; /^default_transaction_read_only[[:space:]]*=/d' "\$D/postgresql.auto.conf"
      printf "primary_conninfo = 'user=pantry_replicator passfile=%s/.pgpass host=%s port=%s application_name=%s'\nprimary_slot_name = '%s'\n" "\$D" "\$H" "\$P" "\$APP_NAME" "\$SLOT" >> "\$D/postgresql.auto.conf"
      rm -f "\$D/pantry-follower.state"
      echo "REJOIN_RESEED_COMPLETE upstream=\$H:\$P"
    volumeMounts: [{name: data, mountPath: /var/lib/postgresql/data}]
  volumes:
  - {name: data, persistentVolumeClaim: {claimName: $REJOIN_PVC}}
EOF

phase=""
for _ in $(seq 1 120); do
  phase="$(k get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  [[ "$phase" == Succeeded || "$phase" == Failed ]] && break
  sleep 5
done
result="$(k logs "$POD" 2>/dev/null | tail -1 || true)"
k delete pod "$POD" --wait=false >/dev/null 2>&1 || true
if [[ "$MODE" == probe ]]; then
  if [[ "$phase" == Succeeded && "$result" == REJOIN_PRIMARY* ]]; then
    k scale statefulset "$REJOIN_STATEFULSET" --replicas=1 >/dev/null
    rm -f "$MARKER"
    say "action=started_standby primary=${result#REJOIN_PRIMARY }"
    exit 0
  fi
  yielded="$(sed -n 's/.*"yielded_at": *\([0-9]*\).*/\1/p' "$MARKER" 2>/dev/null || true)"
  if [[ "$yielded" =~ ^[0-9]+$ ]] && (( $(date +%s) - yielded >= RECLAIM_AFTER )); then
    k scale statefulset "$REJOIN_STATEFULSET" --replicas=1 >/dev/null
    k wait --for=condition=Ready "pod/${REJOIN_STATEFULSET}-0" --timeout=180s >/dev/null 2>&1 || true
    k exec "${REJOIN_STATEFULSET}-0" -c postgres -- psql -U pantry -d pantry -Atc "ALTER SYSTEM SET primary_conninfo = ''" >/dev/null
    k exec "${REJOIN_STATEFULSET}-0" -c postgres -- psql -U pantry -d pantry -Atc "SELECT pg_reload_conf()" >/dev/null
    # Attest freshness: this site was the primary until it yielded and the
    # target replayed its final LSN, so its data is complete. Without this the
    # freshness gate would (correctly, for ordinary replicas) block reclaim.
    now="$(date +%s)"
    k exec "${REJOIN_STATEFULSET}-0" -c postgres -- sh -c "f=/var/lib/postgresql/data/pantry-follower.state; { grep -v -e '^last_streaming=' -e '^disconnected_since=' \$f 2>/dev/null || true; echo last_streaming=$now; echo disconnected_since=$now; } > \$f.n && mv \$f.n \$f" >/dev/null
    rm -f "$MARKER"
    say "action=reclaim_started reason=no_primary_${RECLAIM_AFTER}s_after_handback"
    exit 0
  fi
  say "action=waiting reason=no_unique_primary"
  exit 0
fi
if [[ "$phase" == Succeeded && "$result" == REJOIN_RESEED_COMPLETE* ]]; then
  k scale statefulset "$REJOIN_STATEFULSET" --replicas=1 >/dev/null
  say "action=reseeded_and_started ${result#REJOIN_RESEED_COMPLETE }"
  exit 0
fi
say "action=reseed_failed phase=${phase:-unknown} result=${result:-none}"
exit 1

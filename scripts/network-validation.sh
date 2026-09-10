#!/usr/bin/env bash
set -u

# Read-only checks from a node host network namespace. Override TARGET_* when
# the topology changes; keep failed paths visible in the recorded evidence.
SOURCE_LABEL=${SOURCE_LABEL:-$(hostname -s)}
TARGET_HOME_API=${TARGET_HOME_API:-100.84.89.87}
TARGET_HOME_LAN_API=${TARGET_HOME_LAN_API:-192.168.40.208}
TARGET_CHASEBOT=${TARGET_CHASEBOT:-192.168.40.200}
TARGET_ORACLE=${TARGET_ORACLE:-100.78.181.15}
DNS_SERVICE=${DNS_SERVICE:-10.43.0.10}
failures=0

tcp_check() {
  local label=$1 host=$2 port=$3
  if timeout 3 bash -c "</dev/tcp/${host}/${port}" 2>/dev/null; then
    printf 'PASS tcp %-24s %s:%s\n' "$label" "$host" "$port"
  else
    printf 'FAIL tcp %-24s %s:%s\n' "$label" "$host" "$port"
    failures=$((failures + 1))
  fi
}

http_check() {
  local label=$1 url=$2 expected=$3 code
  code=$(curl -ksS --connect-timeout 3 --max-time 5 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)
  case ",$expected," in
    *",${code},"*) printf 'PASS http %-23s %s -> %s\n' "$label" "$url" "$code" ;;
    *) printf 'FAIL http %-23s %s -> %s (wanted %s)\n' "$label" "$url" "${code:-000}" "$expected"; failures=$((failures + 1)) ;;
  esac
}

printf 'SOURCE %s\n' "$SOURCE_LABEL"
printf '%s\n' '--- management and kubelet ---'
tcp_check home-tailscale-ssh "$TARGET_HOME_API" 22
tcp_check home-lan-ssh "$TARGET_HOME_LAN_API" 22
tcp_check home-tailscale-kubelet "$TARGET_HOME_API" 10250
tcp_check home-lan-api "$TARGET_HOME_LAN_API" 6443
tcp_check chasebot-ssh "$TARGET_CHASEBOT" 22
tcp_check chasebot-kubelet "$TARGET_CHASEBOT" 10250
tcp_check oracle-ssh "$TARGET_ORACLE" 22
tcp_check oracle-kubelet "$TARGET_ORACLE" 10250

printf '%s\n' '--- cluster DNS and application paths ---'
dns_answer=$(dig +time=2 +tries=1 +short "@${DNS_SERVICE}" kubernetes.default.svc.cluster.local A 2>/dev/null | tr '\n' ' ')
if [[ "$dns_answer" == *10.43.0.1* ]]; then
  printf 'PASS dns kubernetes.default.svc -> %s\n' "$dns_answer"
else
  printf 'FAIL dns kubernetes.default.svc -> %s\n' "${dns_answer:-no-answer}"
  failures=$((failures + 1))
fi

http_check pantry-service http://10.43.170.195:3000/ready 200
http_check pantry-pod http://10.42.0.147:3000/ready 200
http_check authentik-service http://10.43.98.83:80/-/health/ready/ 200
http_check authentik-pod http://10.42.0.130:9000/-/health/ready/ 200
http_check grafana-service http://10.43.74.175:3002/api/health 200
http_check grafana-pod http://10.42.0.146:3000/api/health 200

printf '%s\n' '--- home NodePorts ---'
tcp_check traefik-nodeport "$TARGET_HOME_LAN_API" 30817
tcp_check grafana-nodeport "$TARGET_HOME_LAN_API" 31166
tcp_check minecraft-nodeport "$TARGET_HOME_LAN_API" 30339

printf 'RESULT source=%s failures=%s\n' "$SOURCE_LABEL" "$failures"
exit "$([[ $failures -eq 0 ]] && echo 0 || echo 1)"

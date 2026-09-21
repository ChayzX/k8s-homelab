[CmdletBinding()]
param(
  [switch]$ConfirmFence,
  [string]$PostgresContainer = 'pantrybot-canada-postgres'
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmFence) {
  throw 'Refusing to fence Canada without -ConfirmFence; this stops PantryBot writers.'
}

# These are the real production container names started by
# start-canada-production.ps1 via plain `docker run` (verified live,
# 2026-09-21: docker ps -a shows no com.docker.compose.* labels on any
# Canada container - there is no docker-compose.canada.yml in production,
# despite an earlier doc claiming otherwise; see CANADA-ALWAYS-ON.md).
# This list must stay in sync with authority-gate.ps1's $appContainers.
$mutating = @(
  'pantrybot-canada-prod-worker',
  'pantrybot-canada-prod-dispatcher',
  'pantrybot-canada-prod-overlay',
  'pantrybot-canada-prod-api',
  'pantrybot-canada-prod-gateway',
  'pantrybot-canada-prod-private',
  'pantrybot-canada-prod-public'
)

# Fencing is idempotent: a previously stopped PostgreSQL writer is already
# fenced, but application writers must still be verified stopped.
$postgresStatus = (docker inspect --format '{{.State.Status}}' $PostgresContainer 2>$null).Trim()
if ($postgresStatus -ne 'running') {
  $remainingAlreadyFenced = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
  if ($remainingAlreadyFenced.Count -ne 0) { throw "Canada application fence verification failed: $($remainingAlreadyFenced -join ', ')" }
  Write-Output "fence_status=passed site=canada database=stopped applications=stopped postgres_container=$PostgresContainer"
  exit 0
}

# Prevent Docker restart policies from bringing writers back after the fence.
foreach ($name in ($mutating + $PostgresContainer)) {
  docker update --restart=no $name | Out-Null
}
docker stop $mutating 2>$null | Out-Null

# Revoke new writes and terminate existing client sessions before stopping
# the container - a SQL-level fence that takes effect even if the container
# stop is briefly delayed, scoped to this database only (never touches
# Docker or Windows itself).
$sql = @"
ALTER SYSTEM SET default_transaction_read_only = 'on';
SELECT pg_reload_conf();
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE pid <> pg_backend_pid() AND datname = current_database();
"@
docker exec $PostgresContainer psql -U pantry -d pantry -v ON_ERROR_STOP=1 -c $sql | Out-Null
$mode = (docker exec $PostgresContainer psql -U pantry -d pantry -AtX -c 'show default_transaction_read_only').Trim()
if ($mode -ne 'on') { throw "Canada PostgreSQL fence verification failed: read_only=$mode" }

# Stop the database after disabling restart policy and enforcing read-only.
# Stopping the writer is the actual fence; the container must be down
# before promotion, not merely read-only, since a superuser could reverse
# the SQL-level setting while the process is still alive.
docker stop $PostgresContainer 2>$null | Out-Null

$remaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
if ($remaining.Count -ne 0) { throw "Canada application fence verification failed: $($remaining -join ', ')" }
$dbRemaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -eq $PostgresContainer })
if ($dbRemaining.Count -ne 0) { throw "Canada PostgreSQL fence verification failed: container still running" }
Write-Output "fence_status=passed site=canada database=stopped applications=stopped postgres_container=$PostgresContainer"

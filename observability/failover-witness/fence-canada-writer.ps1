[CmdletBinding()]
param(
  [switch]$ConfirmFence,
  [string]$ComposeProject = 'pantrybot-canada',
  [string]$PostgresService = 'postgres',
  [string[]]$SafeStageServices = @('public-site', 'private-site')
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmFence) {
  throw 'Refusing to fence Canada without -ConfirmFence; this stops PantryBot writers.'
}

function Invoke-Docker {
  param([Parameter(Mandatory)][string[]]$Arguments)
  & docker @Arguments
  if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

# Names are derived from the compose project/service labels, never
# hardcoded: 2026-09-21 found the previous hardcoded container-name list
# (e.g. pantrybot-canada-prod-worker) did not match what the current
# docker-compose.canada.yml actually produces (compose's default
# <project>-<service>-<index> naming, since the production-profile services
# have no explicit container_name). That drift meant the fence would report
# "fence_status=passed applications=stopped" while every real production
# container kept running under a different name - a silent false-positive
# fence, the same class of bug found and fixed in the Home/Oracle fencing
# path tonight. Deriving names from labels means a compose file change can
# never silently desync the fence target list again.
function Get-ProjectContainerNames {
  param([Parameter(Mandatory)][string]$ServiceLabel)
  @(& docker ps -a `
      --filter "label=com.docker.compose.project=$ComposeProject" `
      --filter "label=com.docker.compose.service=$ServiceLabel" `
      --format '{{.Names}}')
}

$allContainers = @(& docker ps -a --filter "label=com.docker.compose.project=$ComposeProject" --format '{{.Names}}')
if ($allContainers.Count -eq 0) { throw "No containers found for compose project '$ComposeProject' - refusing to report a fence of nothing as success." }

$postgresContainers = Get-ProjectContainerNames -ServiceLabel $PostgresService
if ($postgresContainers.Count -ne 1) {
  throw "Expected exactly one '$PostgresService' container for project '$ComposeProject', found $($postgresContainers.Count): $($postgresContainers -join ', ')"
}
$PostgresContainer = $postgresContainers[0]

$safeStageNames = @()
foreach ($service in $SafeStageServices) {
  $safeStageNames += Get-ProjectContainerNames -ServiceLabel $service
}
$mutating = @($allContainers | Where-Object { $_ -ne $PostgresContainer -and $_ -notin $safeStageNames })

# Fencing is idempotent: a previously stopped PostgreSQL writer is already
# fenced, but application writers must still be verified stopped.
$postgresStatus = (docker inspect --format '{{.State.Status}}' $PostgresContainer 2>$null).Trim()
if ($postgresStatus -ne 'running') {
  $remainingAlreadyFenced = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
  if ($remainingAlreadyFenced.Count -ne 0) { throw "Canada application fence verification failed: $($remainingAlreadyFenced -join ', ')" }
  Write-Output "fence_status=passed site=canada database=stopped applications=stopped postgres_container=$PostgresContainer mutating_count=$($mutating.Count)"
  exit 0
}

# Prevent Docker restart policies from bringing writers back after the fence.
foreach ($name in ($mutating + $PostgresContainer)) {
  Invoke-Docker @('update', '--restart=no', $name) | Out-Null
}
if ($mutating.Count -gt 0) {
  Invoke-Docker (@('stop') + $mutating) 2>$null | Out-Null
}

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
Invoke-Docker @('stop', $PostgresContainer) 2>$null | Out-Null

$remaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
if ($remaining.Count -ne 0) { throw "Canada application fence verification failed: $($remaining -join ', ')" }
$dbRemaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -eq $PostgresContainer })
if ($dbRemaining.Count -ne 0) { throw "Canada PostgreSQL fence verification failed: container still running" }
Write-Output "fence_status=passed site=canada database=stopped applications=stopped postgres_container=$PostgresContainer mutating_count=$($mutating.Count)"

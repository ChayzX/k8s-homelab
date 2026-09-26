[CmdletBinding()]
param(
  [switch]$ConfirmFence,
  [string]$PostgresContainer = 'pantrybot-canada-postgres',
  # Windows service running the PantryBot-Canada tunnel (8392cd48) connector.
  # It carries only mods/overlay, never the shared PantryBot tunnel 59569621.
  [string]$ConnectorService = 'Cloudflared'
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

# Stop and disable the app tunnel connector so a fenced Canada cannot keep
# answering (or 502-ing) its public routes. Disabled, not just stopped: the
# service is Automatic and would return on reboot. start-canada-production.ps1
# re-enables it on promotion. A missing service counts as fenced.
function Stop-Connector {
  $svc = Get-Service -Name $ConnectorService -ErrorAction SilentlyContinue
  if ($null -eq $svc) { return 'absent' }
  Set-Service -Name $ConnectorService -StartupType Disabled
  if ($svc.Status -ne 'Stopped') { Stop-Service -Name $ConnectorService -Force }
  $svc = Get-Service -Name $ConnectorService
  $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
  if ((Get-Service -Name $ConnectorService).Status -ne 'Stopped') { throw "Canada connector fence verification failed: $ConnectorService still running" }
  return 'stopped'
}

# Native commands never trip $ErrorActionPreference in Windows PowerShell 5,
# so every docker/psql exit code is checked explicitly (#191: a failed
# ALTER SYSTEM was silently ignored for as long as this script existed).
function Invoke-Native([string]$What, [scriptblock]$Command) {
  $out = & $Command
  if ($LASTEXITCODE -ne 0) { throw "Canada fence step failed ($What): exit $LASTEXITCODE" }
  return $out
}
function Invoke-Sql([string]$Sql) {
  Invoke-Native "psql: $Sql" { docker exec $PostgresContainer psql -U pantry -d pantry -AtX -v ON_ERROR_STOP=1 -c $Sql }
}

# Docker Desktop's daemon is not always up (host rebooted, Desktop not
# started, engine restarting). No container can be writing while the daemon
# is unreachable, so that is a *fenced* state, not a failure — but every
# `docker` call below writes the daemon error to stderr, which
# $ErrorActionPreference='Stop' turns into a NativeCommandError that kills
# this script with exit 1. The composite fence treats any non-75 exit as a
# hard failure, so a dark Docker on Canada used to block every promotion at
# every other site (#191, 2026-09-23 outage). Probe the daemon first and
# report the fence that is already in force. The connector is a Windows
# service independent of Docker, so it is still stopped below.
function Test-DockerDaemon {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  } finally {
    $ErrorActionPreference = $previous
  }
}

if (-not (Test-DockerDaemon)) {
  $connector = Stop-Connector
  Write-Output "fence_status=passed site=canada database=daemon_down applications=daemon_down connector=$connector postgres_container=$PostgresContainer"
  exit 0
}

# Fencing is idempotent: a previously stopped PostgreSQL writer is already
# fenced, but application writers must still be verified stopped. A container
# that has never been created makes `docker inspect` fail and return nothing,
# which is also "not running" rather than an error.
$postgresStatus = ''
try {
  $inspected = & docker inspect --format '{{.State.Status}}' $PostgresContainer 2>$null
  if ($LASTEXITCODE -eq 0 -and $null -ne $inspected) { $postgresStatus = ([string]$inspected).Trim() }
} catch {
  $postgresStatus = ''
}
if ($postgresStatus -ne 'running') {
  $remainingAlreadyFenced = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
  if ($remainingAlreadyFenced.Count -ne 0) { throw "Canada application fence verification failed: $($remainingAlreadyFenced -join ', ')" }
  $connector = Stop-Connector
  Write-Output "fence_status=passed site=canada database=stopped applications=stopped connector=$connector postgres_container=$PostgresContainer"
  exit 0
}

# A positively-proven standby cannot write, and cannot promote without the
# lease the fencing site holds: fence only the writers and the connector and
# leave it streaming (#191), so a Canada standby survives other sites'
# failovers. Anything but an explicit 't' falls through to the full fence.
$recovery = ''
try { $recovery = ([string](Invoke-Sql 'select pg_is_in_recovery()')).Trim() } catch { $recovery = '' }
if ($recovery -eq 't') {
  $existingApps = @(Invoke-Native 'docker ps -a' { docker ps -a --format '{{.Names}}' })
  $standbyApps = @($mutating | Where-Object { $_ -in $existingApps })
  foreach ($name in $standbyApps) { Invoke-Native "docker update $name" { docker update --restart=no $name } | Out-Null }
  if ($standbyApps.Count -gt 0) { Invoke-Native 'docker stop apps' { docker stop $standbyApps } | Out-Null }
  $still = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
  if ($still.Count -ne 0) { throw "Canada application fence verification failed: $($still -join ', ')" }
  $connector = Stop-Connector
  Write-Output "fence_status=passed site=canada database=standby applications=stopped connector=$connector postgres_container=$PostgresContainer"
  exit 0
}

# Prevent Docker restart policies from bringing writers back after the fence.
# A container that no longer exists is already not writing; skip it.
$existing = @(Invoke-Native 'docker ps -a' { docker ps -a --format '{{.Names}}' })
$presentApps = @($mutating | Where-Object { $_ -in $existing })
foreach ($name in ($presentApps + $PostgresContainer)) {
  Invoke-Native "docker update $name" { docker update --restart=no $name } | Out-Null
}
if ($presentApps.Count -gt 0) { Invoke-Native 'docker stop apps' { docker stop $presentApps } | Out-Null }

# Revoke new writes and terminate existing sessions before stopping the
# container. One statement per psql call: ALTER SYSTEM cannot run inside the
# implicit transaction a multi-statement -c string creates. pg_reload_conf()
# only signals the postmaster, so wait (bounded) for new sessions to see it.
Invoke-Sql "ALTER SYSTEM SET default_transaction_read_only = 'on'" | Out-Null
Invoke-Sql 'SELECT pg_reload_conf()' | Out-Null
$mode = ''
$deadline = (Get-Date).AddSeconds(15)
do {
  $mode = ([string](Invoke-Sql 'show default_transaction_read_only')).Trim()
  if ($mode -eq 'on') { break }
  Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if ($mode -ne 'on') { throw "Canada PostgreSQL fence verification failed: read_only=$mode" }
Invoke-Sql 'SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database()' | Out-Null

# Stop the database after disabling restart policy and enforcing read-only.
# Stopping the writer is the actual fence; the container must be down
# before promotion, not merely read-only, since a superuser could reverse
# the SQL-level setting while the process is still alive.
Invoke-Native 'docker stop postgres' { docker stop $PostgresContainer } | Out-Null

$remaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
if ($remaining.Count -ne 0) { throw "Canada application fence verification failed: $($remaining -join ', ')" }
$dbRemaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -eq $PostgresContainer })
if ($dbRemaining.Count -ne 0) { throw "Canada PostgreSQL fence verification failed: container still running" }
$connector = Stop-Connector
Write-Output "fence_status=passed site=canada database=stopped applications=stopped connector=$connector postgres_container=$PostgresContainer"

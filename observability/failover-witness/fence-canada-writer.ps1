[CmdletBinding()]
param(
  [switch]$ConfirmFence,
  [string]$PostgresContainer = 'pantrybot-canada-postgres',
  [string[]]$ApplicationContainers = @(
    'pantrybot-canada-prod-worker',
    'pantrybot-canada-prod-dispatcher',
    'pantrybot-canada-prod-overlay',
    'pantrybot-canada-prod-api',
    'pantrybot-canada-prod-gateway',
    'pantrybot-canada-prod-private',
    'pantrybot-canada-prod-public'
  )
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmFence) {
  throw 'Refusing to fence Canada without -ConfirmFence; this stops PantryBot writers.'
}

$mutating = @($ApplicationContainers | Where-Object { $_ -and $_.Trim() })
if ($mutating.Count -eq 0) { throw 'At least one application container is required.' }

function Invoke-Docker {
  param([Parameter(Mandatory)][string[]]$Arguments)
  & docker @Arguments
  if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

# Prevent Docker restart policies from bringing writers back after the fence.
foreach ($name in $mutating + $PostgresContainer) {
  Invoke-Docker @('update','--restart=no',$name) | Out-Null
}
 $stopArgs = @('stop') + $mutating
Invoke-Docker $stopArgs 2>$null | Out-Null

# Stop the database after disabling restart policy. Stopping the writer is the
# fence: it terminates existing sessions and prevents superuser overrides of
# default_transaction_read_only. The old writer must be down before promotion.
Invoke-Docker @('stop',$PostgresContainer) 2>$null | Out-Null
$remaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -in $mutating })
if ($remaining.Count -ne 0) { throw "Canada application fence verification failed: $($remaining -join ', ')" }
$dbRemaining = @(docker ps --format '{{.Names}}' | Where-Object { $_ -eq $PostgresContainer })
if ($dbRemaining.Count -ne 0) { throw "Canada PostgreSQL fence verification failed: container still running" }
Write-Output 'fence_status=passed site=canada database=stopped applications=stopped'

[CmdletBinding()]
param(
  # Operator attests that NO other site was promoted (Canada is still the
  # newest timeline). Restoring Canada after another site promoted would be
  # split-brain; the attestation is required, never inferred (#191).
  [switch]$ConfirmNoOtherPrimary,
  [string]$PostgresContainer = 'pantrybot-canada-postgres',
  [switch]$SkipApps
)

# Undo fence-canada-writer.ps1 after an aborted cutover: restart policy back,
# Postgres started, persisted read-only removed and verified, then the apps
# and the Cloudflared connector via start-canada-production.ps1.
$ErrorActionPreference = 'Stop'
if (-not $ConfirmNoOtherPrimary) {
  throw 'Refusing: pass -ConfirmNoOtherPrimary only after verifying no other site was promoted.'
}
function Invoke-Native([string]$What, [scriptblock]$Command) {
  $out = & $Command
  if ($LASTEXITCODE -ne 0) { throw "Canada restore step failed ($What): exit $LASTEXITCODE" }
  return $out
}
function Invoke-Sql([string]$Sql) {
  Invoke-Native "psql: $Sql" { docker exec $PostgresContainer psql -U pantry -d pantry -AtX -v ON_ERROR_STOP=1 -c $Sql }
}

Invoke-Native 'docker update postgres' { docker update --restart unless-stopped $PostgresContainer } | Out-Null
Invoke-Native 'docker start postgres' { docker start $PostgresContainer } | Out-Null
$deadline = (Get-Date).AddSeconds(60)
do {
  docker exec $PostgresContainer pg_isready -U pantry -d pantry *> $null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)
if ($LASTEXITCODE -ne 0) { throw 'Canada PostgreSQL did not become ready' }

if (([string](Invoke-Sql 'select pg_is_in_recovery()')).Trim() -ne 'f') { throw 'Canada PostgreSQL is not a primary; refusing to restore apps' }
Invoke-Sql 'ALTER SYSTEM RESET default_transaction_read_only' | Out-Null
Invoke-Sql 'SELECT pg_reload_conf()' | Out-Null
$mode = ''
$deadline = (Get-Date).AddSeconds(15)
do {
  $mode = ([string](Invoke-Sql 'show default_transaction_read_only')).Trim()
  if ($mode -eq 'off') { break }
  Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if ($mode -ne 'off') { throw "Canada PostgreSQL still read-only: $mode" }

if (-not $SkipApps) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\ProgramData\PantryBotCanadaPrep\start-canada-production.ps1' | Out-Null
}
Write-Output "restore_status=passed site=canada database=writable apps=$(-not $SkipApps)"

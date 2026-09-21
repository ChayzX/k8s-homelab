$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\ProgramData\PantryBotCanadaPrep\health-monitor.jsonl'
$role = (docker exec pantrybot-canada-postgres psql -U pantry -d pantry -tA -c 'select case when pg_is_in_recovery() then ''standby'' else ''primary'' end;' 2>$null).Trim()
$names = @('pantrybot-canada-postgres','pantrybot-canada-prod-api','pantrybot-canada-prod-gateway','pantrybot-canada-prod-worker','pantrybot-canada-prod-dispatcher','pantrybot-canada-prod-overlay','pantrybot-canada-prod-private','pantrybot-canada-prod-public')
$containers = @()
foreach ($name in $names) {
  $line = docker ps -a --filter "name=^/$name$" --format '{{.Names}}|{{.Status}}'
  $containers += [string]$line
}
$ready = [ordered]@{}
$checks = @(
  @{ Name='public'; Port=18080; Path='/ready' }, @{ Name='private'; Port=18081; Path='/ready' },
  @{ Name='overlay'; Port=18082; Path='/readyz' }, @{ Name='api'; Port=13100; Path='/readyz' },
  @{ Name='gateway'; Port=13101; Path='/readyz' }, @{ Name='dispatcher'; Port=13102; Path='/readyz' }
)
foreach ($pair in $checks) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$($pair.Port)$($pair.Path)" -TimeoutSec 5; $ready[$pair.Name] = [int]$r.StatusCode } catch { $ready[$pair.Name] = 0 }
}
try { $w = Invoke-WebRequest -UseBasicParsing -Uri 'http://100.78.181.15:31421/healthz' -TimeoutSec 5; $witness = [int]$w.StatusCode } catch { $witness = 0 }
[pscustomobject]@{ timestamp=(Get-Date).ToUniversalTime().ToString('o'); database_role=$role; witness_status=$witness; readiness=$ready; containers=$containers } | ConvertTo-Json -Compress | Add-Content -LiteralPath $log

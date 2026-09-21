[CmdletBinding()]
param(
  [string]$PostgresContainer = 'pantrybot-canada-postgres',
  [string]$ProductionStart = 'C:\ProgramData\PantryBotCanadaPrep\start-canada-production.ps1',
  [string]$LogPath = 'C:\ProgramData\PantryBotCanadaPrep\authority-gate.log',
  [string]$EnvPath = 'C:\ProgramData\PantryBotCanadaPrep\canada-production.env',
  [string]$LeaseStatePath = 'C:\ProgramData\PantryBotCanadaPrep\pantry-postgres-lease.json',
  [switch]$Once
)
$ErrorActionPreference = 'Stop'
$appContainers = @('pantrybot-canada-prod-api','pantrybot-canada-prod-gateway','pantrybot-canada-prod-worker','pantrybot-canada-prod-dispatcher','pantrybot-canada-prod-overlay','pantrybot-canada-prod-private','pantrybot-canada-prod-public')
function Log([string]$Message) { Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) $Message" }
function Stop-Apps([string]$Reason) { $running=@(docker ps --format '{{.Names}}'); $active=@($appContainers | Where-Object { $_ -in $running }); if($active.Count -gt 0){ Log "$Reason; stopping: $($active -join ', ')"; docker stop $active *>> $LogPath } else { Log "$Reason; no application containers running" } }
function EnvValue([string]$Name) { $line=Get-Content -LiteralPath $EnvPath | Where-Object { $_ -match ('^'+[regex]::Escape($Name)+'=') } | Select-Object -First 1; if($null -eq $line){ return $null }; return $line.Substring($Name.Length+1) }
function Invoke-Gate {
try {
  $witnessUrl=EnvValue 'PANTRY_WITNESS_URL'; $secret=EnvValue 'PANTRY_WITNESS_SECRET'
  if([string]::IsNullOrWhiteSpace($witnessUrl) -or [string]::IsNullOrWhiteSpace($secret)){ Stop-Apps 'WITNESS_CONFIG_MISSING'; exit 1 }
  $headers=@{ Authorization="Bearer $secret"; 'Content-Type'='application/json' }
  $lease=$null
  if(Test-Path -LiteralPath $LeaseStatePath){ try { $old=Get-Content -Raw -LiteralPath $LeaseStatePath | ConvertFrom-Json; $body=@{site='canada';resource='pantry:postgres';epoch=[int]$old.epoch;token=[string]$old.token} | ConvertTo-Json -Compress; $lease=Invoke-RestMethod -Method Post -Uri ($witnessUrl.TrimEnd('/')+'/v1/authority/renew') -Headers $headers -Body $body -TimeoutSec 5; if($lease.ok -ne $true){$lease=$null} } catch { Log ('WITNESS_RENEW_ERROR '+$_.Exception.Message); $lease=$null } }
  if($null -eq $lease){ try { $body=@{site='canada';resource='pantry:postgres'} | ConvertTo-Json -Compress; $lease=Invoke-RestMethod -Method Post -Uri ($witnessUrl.TrimEnd('/')+'/v1/authority/acquire') -Headers $headers -Body $body -TimeoutSec 5; if([string]::IsNullOrWhiteSpace([string]$lease.token)){ $lease=$null } else { [pscustomobject]@{epoch=[int]$lease.epoch;token=[string]$lease.token} | ConvertTo-Json | Set-Content -LiteralPath $LeaseStatePath -Force } } catch { Log ('WITNESS_ACQUIRE_ERROR '+$_.Exception.Message); $lease=$null } }
  if($null -eq $lease){ Stop-Apps 'DATABASE_AUTHORITY_UNAVAILABLE'; return $false }
  $role=(docker exec $PostgresContainer psql -U pantry -d pantry -tA -c 'select case when pg_is_in_recovery() then ''standby'' else ''primary'' end;' 2>$null).Trim()
  if($role -ne 'primary'){ Stop-Apps "DATABASE_ROLE_$role"; return $false }
  $running=@(docker ps --format '{{.Names}}'); $missing=@($appContainers | Where-Object { $_ -notin $running }); if($missing.Count -gt 0){ Log "PRIMARY_AUTHORITY; starting missing: $($missing -join ', ')"; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ProductionStart *>> $LogPath } else { Log 'PRIMARY_AUTHORITY; all application containers present' }
  return $true
} catch { Log "ERROR $($_.Exception.Message)"; return $false }
}

if($Once){ [void](Invoke-Gate); exit 0 }
# The witness lease is 30 seconds. A one-minute scheduled task can leave a
# healthy site without a lease between invocations. Keep one supervisor alive
# and renew at 10 seconds; on any loss, stop mutating containers immediately.
while($true){ [void](Invoke-Gate); Start-Sleep -Seconds 10 }

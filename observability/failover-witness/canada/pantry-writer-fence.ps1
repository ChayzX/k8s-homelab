[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('home', 'oracle')][string]$Site,
  [switch]$Confirm,
  [switch]$DryRun,
  [string]$Server = '',          # test override of the API server URL
  [int]$TimeoutSeconds = 45,
  [int]$GraceSeconds = 5
)
# Canada's remote fence of a k3s site's PantryBot writer domain (#191).
# PowerShell port of pantry-writer-fence.sh with the same proof rules:
# - every listed Postgres StatefulSet -> replicas 0 AND its -0 pod object gone
#   (graceful delete, never --force: a NotReady node fails instead of passing)
# - DB-writing Deployments + app-cloudflared -> spec 0 AND status 0
# - NotFound = fenced (reported); any other lookup error = hard failure
# - exit 75 ONLY for network silence at the first probe (fenced by lease
#   expiry); refused/auth/mid-fence errors are hard failures (exit 1)
# Lists must match fence-home-direct-from-oracle.sh / fence-oracle-direct.sh.
$ErrorActionPreference = 'Continue'
if ($Confirm -eq $DryRun) { [Console]::Error.WriteLine('fence_status=failed reason=explicit_confirmation_required'); exit 1 }

$lists = @{
  home   = @{
    sts    = @('postgres-authority-standby-home-canada', 'postgres-authority-home-v2', 'postgres-authority-home-failback', 'postgres-authority-home', 'postgres-authority-home-return', 'postgres-authority')
    deploy = @('pantry-private-api', 'pantry-overlay-delivery', 'pantry-twitch-gateway', 'pantry-twitch-dispatcher', 'pantry-chat-worker', 'pantry-private-site', 'app-cloudflared', 'pantry-bot')
  }
  oracle = @{
    sts    = @('postgres-authority-standby-oracle-v2', 'postgres-authority-standby-oracle', 'postgres-authority-standby', 'postgres-authority-standby-home', 'postgres-authority-standby-home-v2', 'postgres-authority-standby-reseed', 'postgres-authority-standby-reseed-v2')
    deploy = @('pantry-private-api', 'pantry-overlay-delivery', 'pantry-twitch-gateway', 'pantry-twitch-dispatcher', 'pantry-chat-worker', 'pantry-private-site', 'app-cloudflared')
  }
}
$protected = @('cloudflared', 'commands-cloudflared', 'pantry-commands-site')
$kubectl = 'C:\Program Files\Docker\Docker\resources\bin\kubectl.exe'
$kubeconfig = "C:\ProgramData\PantryBotCanadaPrep\fence\$Site.kubeconfig"
$scope = "canada-to-$Site"

function Fail([string]$Reason) { [Console]::Error.WriteLine("fence_status=failed scope=$scope reason=$Reason"); exit 1 }
foreach ($d in $lists[$Site].deploy) { if ($d -in $protected) { Fail "protected_deployment_in_fence_list:$d" } }
if (-not (Test-Path -LiteralPath $kubeconfig)) { Fail 'kubeconfig_unreadable' }

function K {
  $base = @("--kubeconfig=$kubeconfig", '--request-timeout=10s', '-n', 'pantry-bot')
  if ($Server) { $base += "--server=$Server" }
  $out = (& $kubectl @base @args 2>&1 | ForEach-Object { "$_" }) -join ' '
  return [pscustomobject]@{ Code = $LASTEXITCODE; Out = $out }
}
# Lookup: returns value, $null for NotFound; anything else exits hard.
function Lookup([string]$Kind, [string]$Name, [string]$Path) {
  $r = K get $Kind $Name -o "jsonpath=$Path"
  if ($r.Code -eq 0) { return [string]$r.Out }
  if ($r.Out -match '\(NotFound\)') { return $null }
  Fail "lookup_failed:$Kind/${Name}:$($r.Out)"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$probe = K version
if ($probe.Code -ne 0) {
  if ($probe.Out -match 'i/o timeout|no route to host|network is unreachable|context deadline exceeded|Client\.Timeout|timed out|connectex: A connection attempt failed' -and $probe.Out -notmatch 'refused') {
    [Console]::Error.WriteLine("writer_fence=unreachable scope=$scope")
    exit 75
  }
  Fail "kubernetes_api_unavailable:$($probe.Out)"
}

$presentSts = @(); $presentDeploy = @(); $absent = @()
foreach ($s in $lists[$Site].sts) { if ($null -eq (Lookup statefulset $s '{.metadata.name}')) { $absent += "statefulset/$s" } else { $presentSts += $s } }
foreach ($d in $lists[$Site].deploy) { if ($null -eq (Lookup deployment $d '{.metadata.name}')) { $absent += "deployment/$d" } else { $presentDeploy += $d } }
if ($DryRun) {
  Write-Output "fence_status=dry_run_ok scope=$scope statefulsets=$($presentSts -join ',') deployments=$($presentDeploy -join ',') absent=$($absent -join ',')"
  exit 0
}

foreach ($s in $presentSts) {
  $r = K patch statefulset $s --type=merge -p '{\"spec\":{\"replicas\":0}}'
  if ($r.Code -ne 0) { Fail "scale_failed:statefulset/${s}:$($r.Out)" }
}
foreach ($d in $presentDeploy) {
  $r = K patch deployment $d --type=merge -p '{\"spec\":{\"replicas\":0}}'
  if ($r.Code -ne 0) { Fail "scale_failed:deployment/${d}:$($r.Out)" }
}
foreach ($s in $presentSts) {
  $r = K delete pod "$s-0" "--grace-period=$GraceSeconds" --wait=false
  if ($r.Code -ne 0 -and $r.Out -notmatch '\(NotFound\)') { Fail "pod_delete_failed:$s-0:$($r.Out)" }
}

$todoSts = $presentSts; $todoDeploy = $presentDeploy
while ($true) {
  $pending = @(); $nextSts = @(); $nextDeploy = @()
  foreach ($s in $todoSts) {
    $ok = $true
    $rep = Lookup statefulset $s '{.spec.replicas}'
    if ($null -ne $rep -and $rep -ne '0') { $ok = $false; $pending += "statefulset/${s}:replicas=$rep" }
    $node = Lookup pod "$s-0" '{.spec.nodeName}'
    if ($null -ne $node) { $ok = $false; $pending += "pod/$s-0:node=$node" }
    if (-not $ok) { $nextSts += $s }
  }
  foreach ($d in $todoDeploy) {
    $v = Lookup deployment $d '{.spec.replicas}/{.status.replicas}'
    if ($null -ne $v -and $v -ne '0/' -and $v -ne '0/0') { $pending += "deployment/${d}:$v"; $nextDeploy += $d }
  }
  $todoSts = $nextSts; $todoDeploy = $nextDeploy
  if ($pending.Count -eq 0) { break }
  if ((Get-Date) -ge $deadline) { Fail "not_fenced_within_${TimeoutSeconds}s:$($pending -join ' ')" }
  Start-Sleep -Seconds 1
}
Write-Output "fence_status=passed scope=$scope statefulsets=$($presentSts -join ',') deployments=$($presentDeploy -join ',') absent=$($absent -join ',')"
exit 0

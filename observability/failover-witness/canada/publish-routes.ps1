[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('home', 'oracle', 'canada')][string]$Active,
  [switch]$Apply,
  [string]$Inputs = 'C:\ProgramData\PantryBotCanadaPrep\cloudflare-route-inputs.json',
  [string]$TokenFile = 'C:\ProgramData\PantryBotCanadaPrep\fence\cloudflare-token',
  [string]$Zone = 'greeniespantry.uk'
)
# PowerShell port of cloudflare_route_adapter.py (tunnel ingress, shared-tunnel
# rules) + switch-app-dns.py (mods/overlay CNAMEs) for Canada, which has no
# real Python (#191). Dry-run unless -Apply. The token is never printed.
# Never touches the shared PantryBot tunnel 59569621 (it is not in the inputs).
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$mode = if ($Apply) { 'apply' } else { 'dry-run' }

$raw = (Get-Content -Raw -LiteralPath $TokenFile).Trim()
if ($raw -match '^[A-Z_]+=(.+)$') { $raw = $Matches[1].Trim() }
if (-not $raw) { Write-Output 'publish_routes=failed reason=empty_token'; exit 1 }
$Headers = @{ Authorization = "Bearer $raw" }
$Api = 'https://api.cloudflare.com/client/v4'

function Cf([string]$Method, [string]$Path, $Body = $null) {
  try {
    $args2 = @{ Method = $Method; Uri = "$Api$Path"; Headers = $Headers; ContentType = 'application/json'; TimeoutSec = 20 }
    if ($null -ne $Body) { $args2.Body = ($Body | ConvertTo-Json -Depth 10 -Compress) }
    $r = Invoke-RestMethod @args2
  } catch {
    $code = $null; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    throw "Cloudflare request failed: $Method $Path http=$code"      # never includes the token
  }
  if ($r.success -ne $true) { throw "Cloudflare rejected request: $Method $Path" }
  return $r.result
}

function Routes($site, [string]$kind) {
  $rk = if ($kind -eq 'application') { 'application_routes' } else { 'commands_routes' }
  $routes = $site.$rk
  if ($null -eq $routes) {
    $hk = if ($kind -eq 'application') { 'application_hostnames' } else { 'commands_hostnames' }
    $ok = if ($kind -eq 'application') { 'application_origin' } else { 'commands_origin' }
    $routes = @($site.$hk | ForEach-Object { [pscustomobject]@{ hostname = $_; service = $site.$ok } })
  }
  foreach ($r in @($routes)) { if (-not $r.hostname -or -not $r.service) { throw "missing $kind route data" } }
  return @($routes | ForEach-Object { [ordered]@{ hostname = [string]$_.hostname; service = [string]$_.service } })
}
function Config([object[]]$Ingress) {
  $list = @($Ingress) + @([ordered]@{ service = 'http_status:404' })
  return [ordered]@{ ingress = $list; 'warp-routing' = [ordered]@{ enabled = $false } }
}
function SiteTunnels($site) {
  $out = [ordered]@{}
  if ($site.connector_tunnel_id) {
    $out[[string]$site.connector_tunnel_id] = Config (@(Routes $site 'application') + @(Routes $site 'commands' | Where-Object { $_ }))
    return $out
  }
  if ($site.application_tunnel_id) { $out[[string]$site.application_tunnel_id] = Config (Routes $site 'application') }
  if ($site.commands_tunnel_id) { $out[[string]$site.commands_tunnel_id] = Config (Routes $site 'commands') }
  return $out
}
function Canon($cfg) {
  # Canonical JSON: ingress entries as hostname,service (service-only for catch-all).
  $ing = @($cfg.ingress | ForEach-Object {
      if ($_.hostname) { [ordered]@{ hostname = [string]$_.hostname; service = [string]$_.service } } else { [ordered]@{ service = [string]$_.service } } })
  $wr = $false; if ($cfg.'warp-routing') { $wr = [bool]$cfg.'warp-routing'.enabled }
  return ([ordered]@{ ingress = $ing; 'warp-routing' = [ordered]@{ enabled = $wr } } | ConvertTo-Json -Depth 10 -Compress)
}

try {
  $data = Get-Content -Raw -LiteralPath $Inputs | ConvertFrom-Json
  $siteNames = @($data.sites.PSObject.Properties.Name)
  if ($Active -notin $siteNames) { throw "unknown active site: $Active" }
  $tunnels = [ordered]@{}
  foreach ($name in $siteNames) {
    $st = SiteTunnels $data.sites.$name
    foreach ($tid in $st.Keys) {
      if (-not $tunnels.Contains($tid)) { $tunnels[$tid] = @{ sites = @(); wanted = $st[$tid] } }
      elseif ((Canon $tunnels[$tid].wanted) -ne (Canon $st[$tid])) { throw "tunnel $tid is shared by sites with different routes" }
      $tunnels[$tid].sites += $name
    }
  }
  $closed = Config @()
  foreach ($tid in $tunnels.Keys) {
    $e = $tunnels[$tid]
    $target = if ($Active -in $e.sites) { $e.wanted } else { $closed }
    $current = (Cf GET "/accounts/$($data.account_id)/cfd_tunnel/$tid/configurations").config
    if ((Canon $current) -ne (Canon $target)) {
      $what = if ($Active -in $e.sites) { 'routes' } else { '404' }
      if ($Apply) { [void](Cf PUT "/accounts/$($data.account_id)/cfd_tunnel/$tid/configurations" @{ config = $target }); $st2 = 'applied' } else { $st2 = 'dry-run' }
      Write-Output "tunnel $tid sites=$($e.sites -join '+') -> $what ($st2)"
    }
  }
  $site = $data.sites.$Active
  $tunnel = if ($site.connector_tunnel_id) { $site.connector_tunnel_id } else { $site.application_tunnel_id }
  $cname = "$tunnel.cfargotunnel.com"
  $zoneId = (Cf GET "/zones?name=$Zone")[0].id
  $ok = $true
  foreach ($r in @($site.application_routes)) {
    $recs = @(Cf GET "/zones/$zoneId/dns_records?name=$($r.hostname)")
    if ($recs.Count -ne 1 -or $recs[0].type -ne 'CNAME') { Write-Output "$($r.hostname): unexpected records - refusing"; $ok = $false; continue }
    $rec = $recs[0]
    if ($rec.content -eq $cname) { Write-Output "$($r.hostname): already -> $cname"; continue }
    if ($Apply) { [void](Cf PATCH "/zones/$zoneId/dns_records/$($rec.id)" @{ content = $cname; proxied = [bool]$rec.proxied }); Write-Output "$($r.hostname): $($rec.content) -> $cname" }
    else { Write-Output "$($r.hostname): $($rec.content) -> $cname (dry-run)" }
  }
  if (-not $ok) { Write-Output "publish_routes=failed active=$Active mode=$mode reason=dns_refused"; exit 1 }
  Write-Output "publish_routes=ok active=$Active mode=$mode"
  exit 0
} catch {
  Write-Output "publish_routes=failed active=$Active mode=$mode reason=$($_.Exception.Message)"
  exit 1
}

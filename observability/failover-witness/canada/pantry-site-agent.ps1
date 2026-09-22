[CmdletBinding()]
param([switch]$Once)
# PantryBot Canada site agent (#191). Canada is the LOWEST-priority site
# (home > oracle > canada). One long-lived loop (NSSM service) that:
#   standby  -> may compete for pantry:postgres only through the same
#               priority/freshness gate as the other sites (120s after its
#               upstream vanished, streamed within 600s, expected sysid), then
#               fences Home+Oracle, promotes, starts apps, publishes routes.
#   primary  -> renews the lease every 5s (background runspace); on loss runs
#               the full Canada fence. Voluntarily HANDS BACK to a healthy,
#               caught-up higher-priority standby (never preempted).
#   stopped  -> rejoin: restart a fenced standby, or back up + reseed an old
#               primary from the unique reachable primary.
# Nothing here prints secrets. Witness reached via Home or Oracle relays.
$ErrorActionPreference = 'Stop'
$Dir = 'C:\ProgramData\PantryBotCanadaPrep'
$Pg = 'pantrybot-canada-postgres'
$Volume = 'pantrybot-canada-postgres-data'
$Image = 'postgres:rehearsal'
$Sysid = '7687975437720940591'
$WitnessUrls = @('http://100.84.89.87:31421', 'http://100.78.181.15:31421')
$Peers = [ordered]@{ home = '100.84.89.87:5432'; oracle = '100.78.181.15:5432' }   # priority order
$PriorityDelay = 120; $MaxStaleness = 600; $MaxSampleAge = 30
$HandbackStable = 600; $HandbackMaxLagBytes = 1048576; $ReclaimAfter = 180
$StateDir = "$Dir\agent"; $JournalPath = "$StateDir\activation.json"; $HandbackPath = "$StateDir\handback.json"
New-Item -ItemType Directory -Force $StateDir | Out-Null

function Log([string]$m) { Write-Output "$((Get-Date).ToUniversalTime().ToString('s'))Z agent $m" }
function EnvValue([string]$n) {
  $l = Get-Content -LiteralPath "$Dir\canada-production.env" | Where-Object { $_ -match ('^' + [regex]::Escape($n) + '=') } | Select-Object -First 1
  if ($null -eq $l) { return $null }; return $l.Substring($n.Length + 1).Trim()
}
function Native([string]$What, [scriptblock]$Cmd) {
  $out = & $Cmd
  if ($LASTEXITCODE -ne 0) { throw "step failed ($What): exit $LASTEXITCODE" }
  return $out
}
function Sql([string]$q) {
  $o = Native "psql" { docker exec $Pg psql -U pantry -d pantry -AtX -v ON_ERROR_STOP=1 -c $q }
  return ((@($o) | ForEach-Object { "$_" }) -join "`n").Trim()   # rows stay newline-separated
}
function PgRunning { (docker inspect --format '{{.State.Running}}' $Pg 2>$null) -eq 'true' }

# ---------- witness ----------
$Secret = EnvValue 'PANTRY_WITNESS_SECRET'
if (-not $Secret) { throw 'PANTRY_WITNESS_SECRET missing' }
function Witness([string]$Path, [hashtable]$Body) {
  $json = $Body | ConvertTo-Json -Compress
  foreach ($u in $WitnessUrls) {
    try {
      return Invoke-RestMethod -Method Post -Uri "$u$Path" -Headers @{ Authorization = "Bearer $Secret" } -ContentType 'application/json' -Body $json -TimeoutSec 5
    } catch {
      $code = $null; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
      if ($code -eq 409) { return $null }                 # held by another site
      if ($null -ne $code) { throw "witness_http_$code" } # real rejection: do not fall through
    }
  }
  throw 'witness_unreachable'
}
function Acquire { $r = Witness '/v1/authority/acquire' @{ site = 'canada'; resource = 'pantry:postgres' }; if ($r -and $r.token) { return $r }; return $null }

# Background lease guard: renews every 5s; $Sync.Lost flips on any failure.
$Sync = [hashtable]::Synchronized(@{ Active = $false; Lost = $false; Epoch = 0; Token = ''; LastOk = [DateTime]::MinValue })
function Start-Guard($lease) {
  $Sync.Active = $true; $Sync.Lost = $false; $Sync.Epoch = [int]$lease.epoch; $Sync.Token = [string]$lease.token; $Sync.LastOk = Get-Date
  $ps = [powershell]::Create()
  [void]$ps.AddScript({
    param($Sync, $Urls, $Secret)
    while ($Sync.Active) {
      $ok = $false
      foreach ($u in $Urls) {
        try {
          $b = @{ site = 'canada'; resource = 'pantry:postgres'; epoch = $Sync.Epoch; token = $Sync.Token } | ConvertTo-Json -Compress
          $r = Invoke-RestMethod -Method Post -Uri "$u/v1/authority/renew" -Headers @{ Authorization = "Bearer $Secret" } -ContentType 'application/json' -Body $b -TimeoutSec 5
          $ok = ($r.ok -eq $true); break
        } catch { $c = $null; try { $c = [int]$_.Exception.Response.StatusCode } catch {}; if ($null -ne $c) { break } }
      }
      if ($ok) { $Sync.LastOk = Get-Date } elseif (((Get-Date) - $Sync.LastOk).TotalSeconds -ge 20) { $Sync.Lost = $true; $Sync.Active = $false }
      Start-Sleep -Seconds 5
    }
  }).AddArgument($Sync).AddArgument($WitnessUrls).AddArgument($Secret)
  $script:GuardHandle = $ps.BeginInvoke(); $script:GuardPs = $ps
}
function Stop-Guard { $Sync.Active = $false; $Sync.Token = '' }
function Assert-Authority { if ($Sync.Lost -or -not $Sync.Active) { throw 'authority_lost' } }

# ---------- journal (same resume rule as oracle_promoter.ActivationJournal) ----------
function Journal-Load { if (Test-Path $JournalPath) { try { return Get-Content -Raw $JournalPath | ConvertFrom-Json } catch {} }; return $null }
function Journal-Record([int]$epoch, [string]$phase, [hashtable]$extra = @{}) {
  $o = [ordered]@{ site = 'canada'; resource = 'pantry:postgres'; epoch = $epoch; system_identifier = $Sysid; phase = $phase; at = (Get-Date).ToUniversalTime().ToString('o') }
  foreach ($k in $extra.Keys) { $o[$k] = $extra[$k] }
  ($o | ConvertTo-Json -Compress) | Set-Content -Encoding ascii "$JournalPath.tmp"; Move-Item -Force "$JournalPath.tmp" $JournalPath
}
function May-Resume([int]$epoch) {
  $p = Journal-Load
  if (-not $p -or $p.site -ne 'canada' -or $p.system_identifier -ne $Sysid) { return $false }
  if ([int]$p.epoch -eq $epoch) { return $p.phase -in @('promoting', 'promoted', 'active') }
  return ($p.phase -eq 'active' -and $epoch -eq [int]$p.epoch + 1)
}

# ---------- local fences / apps ----------
function Fence-Full { Log 'fence=full'; & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$Dir\fence-canada-writer.ps1" -ConfirmFence | ForEach-Object { Log $_ } }
function Start-Apps { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Dir\start-canada-production.ps1" | Out-Null }
function Apps-Ready {
  $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Dir\check-canada-ready.ps1"
  return -not ($out -match 'ERROR')
}
function Stop-AppsAndConnector {
  $apps = @(docker ps --format '{{.Names}}' | Where-Object { $_ -like 'pantrybot-canada-prod-*' })
  foreach ($a in $apps) { docker update --restart=no $a | Out-Null }
  if ($apps.Count) { docker stop $apps | Out-Null }
  Set-Service Cloudflared -StartupType Disabled -ErrorAction SilentlyContinue; Stop-Service Cloudflared -Force -ErrorAction SilentlyContinue
}

# ---------- follower state + gate (same rules as promotion-gate.sh) ----------
function Follower-State {
  $t = docker exec $Pg cat /var/lib/postgresql/data/pantry-follower.state 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  $h = @{}; foreach ($l in $t) { $i = $l.IndexOf('='); if ($i -gt 0) { $h[$l.Substring(0, $i)] = $l.Substring($i + 1) } }; return $h
}
function Gate-Open {
  $s = Follower-State; if (-not $s) { return $false }
  $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  if (-not $s.sampled_at -or $now - [long]$s.sampled_at -gt $MaxSampleAge) { return $false }
  if ($s.role -ne 'standby' -or $s.system_identifier -ne $Sysid -or $s.streaming -ne '0') { return $false }
  if (-not $s.disconnected_since -or -not $s.last_streaming) { return $false }
  return ($now - [long]$s.disconnected_since -ge $PriorityDelay -and $now - [long]$s.last_streaming -le $MaxStaleness)
}

# ---------- promotion (standby -> primary) ----------
function Fence-Remote([string]$site) {
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$Dir\pantry-writer-fence.ps1" -Site $site -Confirm | ForEach-Object { Log $_ }
  return $LASTEXITCODE
}
function Promote-Canada($lease) {
  $epoch = [int]$lease.epoch
  Start-Guard $lease
  try {
    $unreachable = $false
    foreach ($site in @('home', 'oracle')) {
      Assert-Authority
      $rc = Fence-Remote $site
      if ($rc -eq 75) { $unreachable = $true; Log "old_writer=$site lease_expiry" } elseif ($rc -ne 0) { throw "old_writer_fence_failed:$site rc=$rc" }
    }
    if ($unreachable) { Start-Sleep -Seconds 20 }
    Assert-Authority
    if ((Sql 'select pg_is_in_recovery()') -ne 't') { throw 'not_standby' }
    if ((Sql "select coalesce((select status from pg_stat_wal_receiver),'none')") -eq 'streaming') { throw 'receiver_still_streaming' }
    if ((Sql 'select system_identifier from pg_control_system()') -ne $Sysid) { throw 'sysid_mismatch' }
    Journal-Record $epoch 'promoting'
    Assert-Authority
    [void](Sql 'select pg_promote(true, 60)')
    if ((Sql 'select pg_is_in_recovery()') -ne 'f') { throw 'promote_did_not_complete' }
    Journal-Record $epoch 'promoted'
  } catch {
    Log "promotion_aborted reason=$($_.Exception.Message)"
    $primary = $false; try { $primary = (Sql 'select pg_is_in_recovery()') -eq 'f' } catch {}
    if ($primary) { Fence-Full } else { Stop-AppsAndConnector }
    Stop-Guard; return
  }
  # Post-promotion: retried without fencing (DB authority is already ours).
  [void](Sql 'ALTER SYSTEM RESET default_transaction_read_only'); [void](Sql 'SELECT pg_reload_conf()')
  docker update --restart unless-stopped $Pg | Out-Null
  Start-Apps
  $deadline = (Get-Date).AddSeconds(180); while (-not (Apps-Ready) -and (Get-Date) -lt $deadline) { Start-Sleep 5 }
  Publish-Routes
  Journal-Record $epoch 'active'
  Log "promoted epoch=$epoch"
}
function Publish-Routes {
  if (-not (Test-Path "$Dir\publish-routes.ps1") -or -not (Test-Path "$Dir\fence\cloudflare-token")) { Log 'publish_routes=skipped reason=publisher_or_token_missing'; return }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Dir\publish-routes.ps1" -Active canada -Apply | ForEach-Object { Log $_ }
}

# ---------- voluntary hand-back (primary -> higher-priority standby) ----------
$StableSince = @{}
function Handback-Target {
  $rows = @(Sql "select application_name, state, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) from pg_stat_replication" -split "`n")
  $now = Get-Date
  foreach ($site in $Peers.Keys) {
    $row = $rows | Where-Object { $_ -like "pantry-$site-standby|*" } | Select-Object -First 1
    $healthy = $false
    if ($row) { $f = $row.Split('|'); $healthy = ($f[1] -eq 'streaming' -and [double]$f[2] -le $HandbackMaxLagBytes) }
    if ($healthy) { if (-not $StableSince[$site]) { $StableSince[$site] = $now } } else { $StableSince.Remove($site) }
  }
  foreach ($site in $Peers.Keys) { if ($StableSince[$site] -and ($now - $StableSince[$site]).TotalSeconds -ge $HandbackStable) { return $site } }
  return $null
}
function Hand-Back([string]$target) {
  Log "handback=start target=$target"
  Stop-AppsAndConnector
  [void](Sql "ALTER SYSTEM SET default_transaction_read_only = 'on'"); [void](Sql 'SELECT pg_reload_conf()')
  Start-Sleep -Seconds 2
  [void](Sql 'SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database()')
  $final = Sql 'select pg_current_wal_flush_lsn()'
  $deadline = (Get-Date).AddSeconds(60); $caught = $false
  while ((Get-Date) -lt $deadline) {
    $d = Sql "select coalesce((select pg_wal_lsn_diff(replay_lsn, '$final') from pg_stat_replication where application_name = 'pantry-$target-standby' limit 1), -1)"
    if ([double]$d -ge 0) { $caught = $true; break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $caught) {
    Log "handback=aborted reason=target_not_caught_up final=$final"
    [void](Sql 'ALTER SYSTEM RESET default_transaction_read_only'); [void](Sql 'SELECT pg_reload_conf()')
    Start-Apps; return
  }
  Journal-Record $Sync.Epoch 'yielding' @{ final_lsn = $final; target = $target }
  $hp = $Peers[$target]; $h = $hp.Split(':')[0]; $p = $hp.Split(':')[1]
  # Slots are not replicated: make sure ours exists on the target (exists = fine).
  docker exec $Pg psql -X -d "host=$h port=$p user=pantry_replicator replication=true passfile=/var/lib/postgresql/data/.pgpass connect_timeout=5" -c 'CREATE_REPLICATION_SLOT pantry_canada_standby PHYSICAL RESERVE_WAL' 2>$null | Out-Null
  [void](Sql "ALTER SYSTEM SET primary_conninfo = 'user=pantry_replicator passfile=/var/lib/postgresql/data/.pgpass host=$h port=$p application_name=pantry-canada-standby'")
  [void](Sql "ALTER SYSTEM SET primary_slot_name = 'pantry_canada_standby'")
  [void](Sql 'ALTER SYSTEM RESET default_transaction_read_only')
  Native 'standby.signal' { docker exec -u postgres $Pg touch /var/lib/postgresql/data/standby.signal } | Out-Null
  # Stay DOWN until the target is primary: restarting now as its standby would
  # let two standbys cascade from each other and nobody would promote.
  Fence-Full
  Stop-Guard
  Journal-Record $Sync.Epoch 'yielded' @{ final_lsn = $final; target = $target; yielded_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
  Log "handback=yielded target=$target final=$final"
}

# ---------- rejoin (DB container stopped) ----------
function Peer-Primary {
  $found = @()
  foreach ($site in $Peers.Keys) {
    $hp = $Peers[$site]; $h = $hp.Split(':')[0]; $p = $hp.Split(':')[1]
    $o = docker run --rm -v "${Volume}:/d:ro" --entrypoint psql $Image -X -At -d "host=$h port=$p user=pantry_replicator dbname=pantry replication=database connect_timeout=5 passfile=/d/.pgpass" -c 'select pg_is_in_recovery(), (select system_identifier from pg_control_system())' 2>$null
    if ($LASTEXITCODE -eq 0 -and "$o".Trim() -eq "f|$Sysid") { $found += $hp }
  }
  if ($found.Count -eq 1) { return $found[0] }; return $null
}
function Rejoin {
  docker run --rm -v "${Volume}:/d:ro" --entrypoint test $Image -f /d/standby.signal 2>$null
  $hasSignal = ($LASTEXITCODE -eq 0)
  $primary = Peer-Primary
  if ($hasSignal) {
    if ($primary) { docker update --restart unless-stopped $Pg | Out-Null; docker start $Pg | Out-Null; Log "rejoin=started_standby primary=$primary"; return }
    $j = Journal-Load
    if ($j -and $j.phase -eq 'yielded' -and $j.yielded_at -and ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [long]$j.yielded_at) -ge $ReclaimAfter) {
      docker update --restart unless-stopped $Pg | Out-Null; docker start $Pg | Out-Null; Start-Sleep 8
      [void](Sql "ALTER SYSTEM SET primary_conninfo = ''"); [void](Sql 'SELECT pg_reload_conf()')
      $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
      # Attest freshness: Canada was primary until the target replayed its final LSN.
      docker exec -u postgres $Pg sh -c "f=/var/lib/postgresql/data/pantry-follower.state; { grep -v -e '^last_streaming=' -e '^disconnected_since=' `$f 2>/dev/null || true; echo last_streaming=$now; echo disconnected_since=$now; } > `$f.n && mv `$f.n `$f" | Out-Null
      Journal-Record ([int]$j.epoch) 'reclaiming'
      Log 'rejoin=reclaim_started reason=no_primary_after_handback'
    }
    return
  }
  if (-not $primary) { return }
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
  docker run --rm -v "${Volume}:/d:ro" -v "$Dir\backups:/b" --entrypoint tar $Image czf "/b/canada-pgdata-pre-rejoin-$stamp.tgz" -C /d . | Out-Null
  $pw = (docker run --rm -v "${Volume}:/d:ro" --entrypoint sh $Image -c "sed -n 's/^\*:\*:\*:pantry_replicator://p' /d/.pgpass" 2>$null | Select-Object -First 1)
  if (-not $pw) { Log 'rejoin=blocked reason=no_passfile'; return }
  $h = $primary.Split(':')[0]; $p = $primary.Split(':')[1]
  $out = $pw | docker run --rm -i -v "${Volume}:/var/lib/postgresql/data" -v "$Dir\canada-reseed-from-primary.sh:/reseed.sh:ro" -e "PRIMARY_HOST=$h" -e "PRIMARY_PORT=$p" -e PRIMARY_SLOT_NAME=pantry_canada_standby --entrypoint sh $Image /reseed.sh 2>&1
  if (($out | Select-Object -Last 1) -eq 'RESEED_COMPLETE') {
    docker update --restart unless-stopped $Pg | Out-Null; docker start $Pg | Out-Null
    Log "rejoin=reseeded_and_started upstream=$primary backup=canada-pgdata-pre-rejoin-$stamp.tgz"
  } else { Log "rejoin=reseed_failed" }
}

# ---------- main loop ----------
function Tick {
  if (-not (PgRunning)) { Stop-Guard; Rejoin; return }
  $rec = Sql 'select pg_is_in_recovery()'
  if ($rec -eq 'f') {
    if ($Sync.Active) {
      if ($Sync.Lost) { Log 'lease=lost'; Stop-Guard; Fence-Full; Journal-Record $Sync.Epoch 'fenced'; return }
      $t = Handback-Target
      if ($t) { Hand-Back $t }
      return
    }
    $lease = $null; try { $lease = Acquire } catch { Log "witness=$($_.Exception.Message)" }
    if ($lease -and (May-Resume ([int]$lease.epoch))) { Start-Guard $lease; Journal-Record ([int]$lease.epoch) 'active'; Log "resumed epoch=$($lease.epoch)"; return }
    Log 'primary_without_authority'; Fence-Full; return
  }
  # standby
  Stop-Guard
  $j = Journal-Load
  $reclaim = $false
  if ($j -and $j.phase -eq 'reclaiming') { $reclaim = $true }
  elseif ($j -and $j.phase -eq 'yielded' -and $j.yielded_at) {
    $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [long]$j.yielded_at
    if ($age -ge $ReclaimAfter -and -not (Peer-Primary)) { $reclaim = $true; Log "reclaim=eligible yielded_for=${age}s no_primary" }
  }
  if (-not $reclaim -and -not (Gate-Open)) { return }
  if ($reclaim) { [void](Sql "ALTER SYSTEM SET primary_conninfo = ''"); [void](Sql 'SELECT pg_reload_conf()'); Start-Sleep 3 }
  $lease = $null; try { $lease = Acquire } catch { Log "witness=$($_.Exception.Message)"; return }
  if (-not $lease) { return }
  Log "lease=acquired epoch=$($lease.epoch)"
  Promote-Canada $lease
}

Log "started witness=$($WitnessUrls -join ',')"
do {
  try { Tick } catch { Log "tick_error $($_.Exception.Message)" }
  if (-not $Once) { Start-Sleep -Seconds 5 }
} while (-not $Once)

# Canada host loop for pantry-standby-follower.sh (#191): one follower
# iteration inside the Postgres container every 5s (local socket access,
# state in PGDATA). Installed as NSSM service "PantryBot Canada standby follower".
$ErrorActionPreference = 'Continue'
$dir = 'C:\ProgramData\PantryBotCanadaPrep'
$peers = 'home=100.84.89.87:5432 oracle=100.78.181.15:5432'
while ($true) {
  $script = (Get-Content -Raw "$dir\pantry-standby-follower.sh") -replace "`r", ''
  $running = (docker inspect --format '{{.State.Running}}' pantrybot-canada-postgres 2>$null)
  if ($running -eq 'true') {
    $script | docker exec -i -u postgres -e SITE=canada -e "PEERS=$peers" -e ONESHOT=1 pantrybot-canada-postgres sh -s
  }
  Start-Sleep -Seconds 5
}

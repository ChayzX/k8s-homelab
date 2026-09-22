$ErrorActionPreference='Continue'
$net='pantrybot-canada-production'
docker network create $net 2>$null | Out-Null
docker network connect $net pantrybot-canada-postgres 2>$null
# Image tags: CI (pantry-bot deploy workflow) writes canada-images.json on every
# component deploy so Canada never drifts from Home (#379). The map below is the
# fallback if that file is missing or unreadable.
$imagesFile = 'C:\ProgramData\PantryBotCanadaPrep\canada-images.json'
$img=@{ api='ghcr.io/chayzx/pantry-bot-api:514998193de5a4dcd2daa1a96b8c5bc30d8b83f2'; gateway='ghcr.io/chayzx/pantry-bot-gateway:9130e5dfd53f1d3fe19316272847df4256c82787'; worker='ghcr.io/chayzx/pantry-bot-worker:5761ed428fa58ed0d7d685fc456d2245d040754f'; dispatcher='ghcr.io/chayzx/pantry-bot-dispatcher:9130e5dfd53f1d3fe19316272847df4256c82787'; overlay='ghcr.io/chayzx/pantry-bot-overlay:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9'; private='ghcr.io/chayzx/pantry-bot-private-site:514998193de5a4dcd2daa1a96b8c5bc30d8b83f2'; public='ghcr.io/chayzx/pantry-bot-public-site:735df1ecfe73f57771617977436dc0804c7784f6' }
if (Test-Path -LiteralPath $imagesFile) {
  try {
    $fromFile = Get-Content -Raw -LiteralPath $imagesFile | ConvertFrom-Json
    foreach ($role in @($img.Keys)) {
      $v = $fromFile.$role
      if ($v -and $v -is [string] -and $v.StartsWith('ghcr.io/chayzx/pantry-bot')) { $img[$role] = $v }
    }
  } catch { Write-Output "canada-images.json unreadable; using built-in tags: $($_.Exception.Message)" }
}
foreach($n in @('api','gateway','worker','dispatcher','overlay','private','public')) { docker rm -f "pantrybot-canada-prod-$n" 2>$null | Out-Null }
$common=@('--network',$net,'--env-file','C:\ProgramData\PantryBotCanadaPrep\canada-production.env','--label','pantrybot.production-authority=false','--restart','unless-stopped')
docker run -d --name pantrybot-canada-prod-api @common -e DB_PATH=/tmp/pantry.db -e HTTP_PORT=3000 -e WS_PORT=8080 -e PANTRY_RUNTIME_ROLE=api -e PANTRY_INSTANCE_ID=canada-api -p 127.0.0.1:13100:3000 $img.api
docker run -d --name pantrybot-canada-prod-gateway @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=gateway -e PANTRY_INSTANCE_ID=canada-gateway -e PANTRY_LEASE_MS=30000 -e PANTRY_POLL_MS=1000 -e PANTRY_WITNESS_LEASE_MS=30000 -e PANTRY_WITNESS_RESOURCE=pantry:twitch:ingress -e HTTP_PORT=3000 -e WS_PORT=8080 -e HEALTH_PORT=3001 -p 127.0.0.1:13101:3001 $img.gateway
docker run -d --name pantrybot-canada-prod-worker @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=worker -e PANTRY_INSTANCE_ID=canada-worker -e PANTRY_LEASE_MS=30000 -e PANTRY_POLL_MS=1000 -e PANTRY_WITNESS_LEASE_MS=30000 -e PANTRY_WITNESS_RESOURCE=pantry:site:canada:workers -e HEALTH_PORT=3001 -p 127.0.0.1:13103:3001 $img.worker
docker run -d --name pantrybot-canada-prod-dispatcher @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=dispatcher -e PANTRY_INSTANCE_ID=canada-dispatcher -e PANTRY_LEASE_MS=30000 -e PANTRY_POLL_MS=1000 -e PANTRY_WITNESS_LEASE_MS=30000 -e PANTRY_WITNESS_RESOURCE=pantry:twitch:outbound -e HTTP_PORT=3000 -e WS_PORT=8080 -e HEALTH_PORT=3001 -p 127.0.0.1:13102:3001 $img.dispatcher
docker run -d --name pantrybot-canada-prod-overlay @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=overlay -e PANTRY_INSTANCE_ID=canada-overlay -e PANTRY_LEASE_MS=30000 -e PANTRY_POLL_MS=1000 -e PANTRY_WITNESS_RESOURCE=pantry -e PANTRY_OVERLAY_RESOURCE=pantry:overlay -e HTTP_PORT=8080 -p 127.0.0.1:18082:8080 $img.overlay
docker run -d --name pantrybot-canada-prod-private @common -e PANTRY_SITE_ID=canada -e PRIVATE_SITE_PORT=3001 -e PRIVATE_API_ORIGIN=http://pantrybot-canada-prod-api:3000 -p 127.0.0.1:18081:3001 $img.private
docker run -d --name pantrybot-canada-prod-public @common -e PANTRY_SITE_ID=canada -e PUBLIC_SITE_PORT=3000 -p 127.0.0.1:18080:3000 $img.public
# fence-canada-writer.ps1 disables the PantryBot-Canada tunnel connector; bring it back with the apps.
Set-Service -Name Cloudflared -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name Cloudflared -ErrorAction SilentlyContinue
Start-Sleep -Seconds 12
Get-Process docker -ErrorAction SilentlyContinue | Out-Null
Write-Output STARTED

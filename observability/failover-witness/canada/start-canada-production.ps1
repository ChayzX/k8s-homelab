$ErrorActionPreference='Continue'
$net='pantrybot-canada-production'
docker network create $net 2>$null | Out-Null
docker network connect $net pantrybot-canada-postgres 2>$null
$img=@{ api='ghcr.io/chayzx/pantry-bot-api:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9'; gateway='ghcr.io/chayzx/pantry-bot-gateway:9f336a8b88ebfaef0aac89d7963823547d1b553a'; worker='ghcr.io/chayzx/pantry-bot-worker:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9'; dispatcher='ghcr.io/chayzx/pantry-bot-dispatcher:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9'; overlay='ghcr.io/chayzx/pantry-bot-overlay:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9'; private='ghcr.io/chayzx/pantry-bot-private-site:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9'; public='ghcr.io/chayzx/pantry-bot-public-site:9ac195e05dc21f4a18ccb1ef6a4f85db580dffc9' }
foreach($n in @('api','gateway','worker','dispatcher','overlay','private','public')) { docker rm -f "pantrybot-canada-prod-$n" 2>$null | Out-Null }
$common=@('--network',$net,'--env-file','C:\ProgramData\PantryBotCanadaPrep\canada-production.env','--label','pantrybot.production-authority=false','--restart','unless-stopped')
docker run -d --name pantrybot-canada-prod-api @common -e DB_PATH=/tmp/pantry.db -e HTTP_PORT=3000 -e WS_PORT=8080 -e PANTRY_RUNTIME_ROLE=api -e PANTRY_INSTANCE_ID=canada-api -p 127.0.0.1:13100:3000 $img.api
docker run -d --name pantrybot-canada-prod-gateway @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=gateway -e PANTRY_INSTANCE_ID=canada-gateway -e HTTP_PORT=3000 -e WS_PORT=8080 -e HEALTH_PORT=3001 -p 127.0.0.1:13101:3001 $img.gateway
docker run -d --name pantrybot-canada-prod-worker @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=worker -e PANTRY_INSTANCE_ID=canada-worker -e HEALTH_PORT=3001 $img.worker
docker run -d --name pantrybot-canada-prod-dispatcher @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=dispatcher -e PANTRY_INSTANCE_ID=canada-dispatcher -e HTTP_PORT=3000 -e WS_PORT=8080 -e HEALTH_PORT=3001 -p 127.0.0.1:13102:3001 $img.dispatcher
docker run -d --name pantrybot-canada-prod-overlay @common -e DB_PATH=/tmp/pantry.db -e PANTRY_RUNTIME_ROLE=overlay -e PANTRY_INSTANCE_ID=canada-overlay -e HTTP_PORT=8080 -p 127.0.0.1:18082:8080 $img.overlay
docker run -d --name pantrybot-canada-prod-private @common -e PANTRY_SITE_ID=canada -e PRIVATE_SITE_PORT=3001 -e PRIVATE_API_ORIGIN=http://pantrybot-canada-prod-api:3000 -p 127.0.0.1:18081:3001 $img.private
docker run -d --name pantrybot-canada-prod-public @common -e PANTRY_SITE_ID=canada -e PUBLIC_SITE_PORT=3000 -p 127.0.0.1:18080:3000 $img.public
Start-Sleep -Seconds 12
Get-Process docker -ErrorAction SilentlyContinue | Out-Null
Write-Output STARTED

# (Re)create the Canada log shipper container (#191). Idempotent.
$ErrorActionPreference = 'Continue'
$dir = 'C:\ProgramData\PantryBotCanadaPrep'
docker rm -f pantrybot-canada-alloy 2>$null | Out-Null
docker run -d --name pantrybot-canada-alloy --restart unless-stopped `
  -v /var/run/docker.sock:/var/run/docker.sock:ro `
  -v "$dir\canada-alloy.alloy:/etc/alloy/config.alloy:ro" `
  --label pantrybot.production-authority=false `
  grafana/alloy:v1.8.3 run --storage.path=/var/lib/alloy/data /etc/alloy/config.alloy

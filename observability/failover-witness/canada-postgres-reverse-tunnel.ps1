# Persistent Canada -> Oracle PostgreSQL transport (activation-window only).
# Oracle's isolated standby-prep manifest consumes loopback port 25442.
$ErrorActionPreference='Stop'
$ssh='C:\Windows\System32\OpenSSH\ssh.exe'
$key='C:\Users\BotAdmin\.ssh\pantrybot-canada-rsa'
& $ssh -NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -i $key -R 127.0.0.1:25442:127.0.0.1:15432 ubuntu@100.78.181.15
exit $LASTEXITCODE

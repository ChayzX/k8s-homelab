# Direct Canada -> GCP witness tunnel (#191), independent of Home and Oracle,
# so Canada's last-resort path works when BOTH other sites are down.
# Prerequisite (owner): the key C:\ProgramData\PantryBotCanadaPrep\witness-ssh\id_ed25519.pub
# is authorized for OS Login user $OsLoginUser on the witness VM.
param([string]$OsLoginUser = 'sa_105559435168833655240')
$ErrorActionPreference = 'Stop'
$nssm = 'C:\Program Files\WinGet\Links\nssm.exe'
$svc = 'PantryBot Canada witness tunnel'
$dir = 'C:\ProgramData\PantryBotCanadaPrep'
& $nssm install $svc 'C:\Windows\System32\OpenSSH\ssh.exe'
& $nssm set $svc AppParameters "-NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$dir\witness-ssh\known_hosts -o IdentitiesOnly=yes -i $dir\witness-ssh\id_ed25519 -L 127.0.0.1:18765:127.0.0.1:8765 $OsLoginUser@136.113.178.106"
& $nssm set $svc AppDirectory $dir
& $nssm set $svc AppStdout "$dir\service-witness-tunnel.out.log"
& $nssm set $svc AppStderr "$dir\service-witness-tunnel.err.log"
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 5000
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc ObjectName LocalSystem
& $nssm start $svc | Out-Null
Start-Sleep 5
try { 'witness_direct=' + (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 http://127.0.0.1:18765/healthz).StatusCode } catch { 'witness_direct=unreachable' }

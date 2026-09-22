# Run on Canada (truffles) as admin. Mirrors install-nssm-tunnel.ps1 (Oracle) for Home.
$ErrorActionPreference='Stop'
$nssm='C:\Program Files\WinGet\Links\nssm.exe'
$svc='PantryBot Canada to Home replication tunnel'
$dir='C:\ProgramData\PantryBotCanadaPrep'
& $nssm install $svc 'C:\Windows\System32\OpenSSH\ssh.exe'
& $nssm set $svc AppParameters "-NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$dir\replication-ssh\known_hosts -o HostKeyAlias=minecraftmachine -o IdentitiesOnly=yes -i $dir\replication-ssh\id_ed25519 -R 127.0.0.1:25442:127.0.0.1:15432 pantry-replication-relay@100.84.89.87"
& $nssm set $svc AppDirectory $dir
& $nssm set $svc AppStdout "$dir\service-home-tunnel.out.log"
& $nssm set $svc AppStderr "$dir\service-home-tunnel.err.log"
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 5000
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc ObjectName LocalSystem
& $nssm start $svc | Out-Null
'service-registered'

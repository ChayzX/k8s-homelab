$ErrorActionPreference = 'Stop'
$nssm = 'C:\Program Files\WinGet\Links\nssm.exe'
$svc = 'PantryBot Canada standby follower'
$dir = 'C:\ProgramData\PantryBotCanadaPrep'
& $nssm install $svc 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
& $nssm set $svc AppParameters "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File $dir\canada-standby-follower.ps1"
& $nssm set $svc AppDirectory $dir
& $nssm set $svc AppStdout "$dir\service-follower.out.log"
& $nssm set $svc AppStderr "$dir\service-follower.err.log"
& $nssm set $svc AppRotateFiles 1
& $nssm set $svc AppRotateBytes 5242880
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 5000
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc ObjectName LocalSystem
& $nssm start $svc | Out-Null
'follower-service-started'

$checks=@{
 api='http://127.0.0.1:13100/readyz'; gateway='http://127.0.0.1:13101/readyz'; dispatcher='http://127.0.0.1:13102/readyz'; overlay='http://127.0.0.1:18082/readyz'; private='http://127.0.0.1:18081/ready'; public='http://127.0.0.1:18080/ready'
}
foreach($n in $checks.Keys){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri $checks[$n] -TimeoutSec 8; Write-Output "$n=$($r.StatusCode)" } catch { Write-Output "$n=ERROR" } }

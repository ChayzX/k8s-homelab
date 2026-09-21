foreach($n in @('api','gateway','worker','dispatcher','overlay','private','public')) { docker update --restart no "pantrybot-canada-prod-$n" 2>$null | Out-Null; docker stop "pantrybot-canada-prod-$n" 2>$null | Out-Null }
Write-Output STOPPED_FAIL_CLOSED

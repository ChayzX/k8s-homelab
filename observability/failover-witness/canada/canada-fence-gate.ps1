# Forced-command gate for remote PantryBot fence keys on Canada (#191).
#
# administrators_authorized_keys pins a fence key to this script with
# command="..." and from="<caller IP>", so the key can do exactly two things
# no matter what the caller sends:
#   - dry-run: prove transport + that the fence script exists (no writes)
#   - fence:   run fence-canada-writer.ps1 -ConfirmFence
# The requested command (SSH_ORIGINAL_COMMAND) only selects between those;
# its paths and arguments are never executed.
$ErrorActionPreference = 'Stop'
$fence = 'C:\ProgramData\PantryBotCanadaPrep\fence-canada-writer.ps1'
$requested = [string]$env:SSH_ORIGINAL_COMMAND

if ($requested -match 'fence-canada-writer\.ps1' -and $requested -match '-ConfirmFence\s*$' -and $requested -notmatch 'Test-Path') {
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $fence -ConfirmFence
  exit $LASTEXITCODE
}
if ($requested -match 'Test-Path' -and $requested -notmatch '-ConfirmFence') {
  if (Test-Path -LiteralPath $fence) { exit 0 }
  exit 1
}
[Console]::Error.WriteLine('canada_fence_gate=denied')
exit 1

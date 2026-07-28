# Attach the Interception class filter to ONE device without rebooting.
#
# After installing the Interception driver, the keyboard-class upper filter only
# binds to a device when that device's stack is (re)built. Restarting just the
# target device does that in ~2 seconds — and, crucially, leaves every other
# keyboard (including the built-in one) untouched and unfiltered.
#
# Usage (elevated):
#   .\restart-keypad-device.ps1 -InstanceId 'HID\{...}_DEV_VID&02045E_PID&0040...'
#
# Find your device's instance id with:
#   python3 remap.py detect
#   # or: Get-PnpDevice | Where-Object { $_.FriendlyName -match 'keypad' }
param(
    [Parameter(Mandatory = $true)]
    [string]$InstanceId
)

$log = Join-Path $PSScriptRoot 'restart-dev.log'
"=== $(Get-Date) restarting $InstanceId" | Out-File $log
pnputil /restart-device "$InstanceId" 2>&1 | Out-File $log -Append
Start-Sleep 3
$dev = Get-PnpDevice -InstanceId $InstanceId -ErrorAction SilentlyContinue
"post-restart status: $($dev.Status)" | Out-File $log -Append
Get-Content $log

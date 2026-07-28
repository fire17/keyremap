# Kill every RemapAgent instance (they hold low-level keyboard hooks).
# Loops because instances were observed respawning/reporting stale PIDs.
for ($i = 0; $i -lt 15; $i++) {
    $p = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -match 'RemapAgent\.ps1' -and $_.ProcessId -ne $PID }
    if (-not $p) { break }
    $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
}
$left = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -match 'RemapAgent\.ps1' -and $_.ProcessId -ne $PID }
if ($left) { Write-Output ("STILL: " + (@($left).ProcessId -join ',')) }
else { Write-Output "clean: no RemapAgent processes" }

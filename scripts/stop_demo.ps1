# Stop what scripts\start_demo.ps1 started (C1.9). Only this project's processes, matched by their module name. Data stays:
# containers are stopped, never removed, so Kafka topics and PostgreSQL survive.
#
#   powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1 -KeepDocker     # leave Kafka and PostgreSQL running
#   powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1 -KeepConsole    # leave the console running
param(
    [switch]$KeepDocker,
    [switch]$KeepConsole
)

$root = Split-Path -Parent $PSScriptRoot
$t0 = Get-Date

function Elapsed { '{0,5:N1}s' -f ((Get-Date) - $t0).TotalSeconds }

function Show-Step([int]$done, [int]$total, [string]$note) {
    $filled = [int][math]::Round(30 * $done / $total)
    $bar = ('#' * $filled) + ('.' * (30 - $filled))
    Write-Host ("  [{0}] {1,3:N0}% {2}/{3}  {4}  {5}" -f $bar, (100 * $done / $total), $done, $total, (Elapsed), $note)
}

# Upstream first, so nothing downstream stops while its input still moves.
$services = @(
    @{ name = 'live producer'; pattern = '-m producers[.]usgs_iv_producer' },
    @{ name = 'replay'; pattern = '-m producers[.]replay_producer' },
    @{ name = 'Faust workers'; pattern = 'faust_app.*worker' },
    @{ name = 'forecaster'; pattern = '-m streams[.]forecaster' },
    @{ name = 'sink'; pattern = '-m storage[.]sink' },
    @{ name = 'notifier'; pattern = '-m alerting[.]notifier' }
)
if (-not $KeepConsole) { $services += @{ name = 'console'; pattern = '-m dashboard[.]flow_monitor' } }

Write-Host ("[start {0:HH:mm:ss}] stop_demo: {1} kinds of process{2}" -f $t0, $services.Count, $(if ($KeepDocker) { '' } else { ' and the Docker stack' }))
$total = $services.Count + $(if ($KeepDocker) { 0 } else { 1 })
$step = 0
$stopped = 0
foreach ($s in $services) {
    $found = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|faust)' -and $_.CommandLine -match $s.pattern })
    foreach ($p in $found) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $stopped += $found.Count
    $step++
    Show-Step $step $total ("{0}: {1} process(es) stopped" -f $s.name, $found.Count)
}

$left = @(foreach ($s in $services) {
    Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|faust)' -and $_.CommandLine -match $s.pattern }
})

$composeExit = 0
if (-not $KeepDocker) {
    Push-Location (Join-Path $root 'docker')
    & cmd /c "docker compose stop >nul 2>&1"
    $composeExit = $LASTEXITCODE
    Pop-Location
    $step++
    Show-Step $step $total "docker compose stop (exit $composeExit)"
}

$ok = $left.Count -eq 0 -and $composeExit -eq 0
$status = if ($ok) { "PASS: $stopped process(es) stopped, none left" } else { "FAIL: $($left.Count) process(es) left, compose exit $composeExit" }
Write-Host ("[done {0:HH:mm:ss}] stop_demo: {1}{2} (elapsed {3})" -f (Get-Date), $status, $(if ($KeepDocker) { '; Docker left running' } else { '; containers stopped, data kept' }), (Elapsed))
if ($ok) { exit 0 } else { exit 1 }

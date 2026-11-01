# Start everything the demo needs (C1.9): Docker stack, 3 Faust workers, sink, forecaster, notifier, live gauge and rain producers, console.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -NoLiveProducer
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -Venv C:\path\to\venv     (or set DEWS_VENV)
#
# Safe to run twice: whatever is already running is left alone. Background processes run hidden, with their output in
# gate_output\<name>.log and .err. Sends nothing: the notifier only acts on alerts.approved, after a person approves.
# Stop with scripts\stop_demo.ps1.
param(
    [switch]$NoLiveProducer,
    [int]$TimeoutS = 240,
    [string]$Venv = $env:DEWS_VENV     # folder of the virtual environment; default .venv in the repo, else ..\spaenv
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Venv) {
    $Venv = Join-Path $root '.venv'
    if (-not (Test-Path $Venv)) { $Venv = Join-Path (Split-Path -Parent $root) 'spaenv' }
}
$python = Join-Path $Venv 'Scripts\python.exe'
$faust = Join-Path $Venv 'Scripts\faust.exe'
if (-not (Test-Path $python)) { throw "no Python in $Venv : pass -Venv <folder> or set DEWS_VENV" }
$logs = Join-Path $root 'gate_output'
$t0 = Get-Date

function Elapsed { '{0,5:N1}s' -f ((Get-Date) - $t0).TotalSeconds }

function Show-Step([int]$done, [int]$total, [string]$note) {
    $filled = [int][math]::Round(30 * $done / $total)
    $bar = ('#' * $filled) + ('.' * (30 - $filled))
    Write-Host ("  [{0}] {1,3:N0}% {2}/{3}  {4}  {5}" -f $bar, (100 * $done / $total), $done, $total, (Elapsed), $note)
}

function Find-Service([string]$pattern) {
    @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|faust)' -and $_.CommandLine -match $pattern })
}

function Start-Service-Hidden([string]$name, [string]$pattern, [string]$exe, [string[]]$arguments) {
    if ((Find-Service $pattern).Count -gt 0) { return 'already running' }
    Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "$name.log") -RedirectStandardError (Join-Path $logs "$name.err") | Out-Null
    return 'started'
}

function Test-Healthy([string[]]$containers) {
    foreach ($c in $containers) {
        $state = & docker inspect -f '{{.State.Health.Status}}' $c 2>$null
        if ($state -ne 'healthy') { return $false }
    }
    return $true
}

$services = @(
    @{ name = 'faust_6066'; pattern = 'faust_app.*web-port 6066'; exe = $faust; args = @('-A', 'streams.faust_app', 'worker', '-l', 'info', '--web-port', '6066') },
    @{ name = 'faust_6067'; pattern = 'faust_app.*web-port 6067'; exe = $faust; args = @('-A', 'streams.faust_app', 'worker', '-l', 'info', '--web-port', '6067') },
    @{ name = 'faust_6068'; pattern = 'faust_app.*web-port 6068'; exe = $faust; args = @('-A', 'streams.faust_app', 'worker', '-l', 'info', '--web-port', '6068') },
    @{ name = 'sink'; pattern = '-m storage[.]sink'; exe = $python; args = @('-m', 'storage.sink') },
    @{ name = 'forecaster'; pattern = '-m streams[.]forecaster'; exe = $python; args = @('-m', 'streams.forecaster') },
    @{ name = 'notifier'; pattern = '-m alerting[.]notifier'; exe = $python; args = @('-m', 'alerting.notifier') },
    @{ name = 'console'; pattern = '-m dashboard[.]flow_monitor'; exe = $python; args = @('-m', 'dashboard.flow_monitor') }
)
if (-not $NoLiveProducer) {
    $services += @{ name = 'mrms_producer'; pattern = '-m producers[.]mrms_qpe_producer'; exe = $python; args = @('-m', 'producers.mrms_qpe_producer') }
    $services += @{ name = 'usgs_producer'; pattern = '-m producers[.]usgs_iv_producer'; exe = $python; args = @('-m', 'producers.usgs_iv_producer') }
}

Write-Host ("[start {0:HH:mm:ss}] start_demo: Docker stack and {1} background processes" -f $t0, $services.Count)
if (-not (Test-Path $python)) { Write-Host "[done] start_demo: FAIL: no virtual environment at $python"; exit 1 }
New-Item -ItemType Directory -Force $logs | Out-Null
$total = $services.Count + 3
$step = 0

# Native commands write progress to stderr; with 'Stop' that would abort the script, so judge them by exit code.
$ErrorActionPreference = 'Continue'
Push-Location (Join-Path $root 'docker')
& cmd /c "docker compose up -d >""$logs\compose.log"" 2>&1"
$composeExit = $LASTEXITCODE
Pop-Location
$step++
Show-Step $step $total "docker compose up -d (exit $composeExit)"
if ($composeExit -ne 0) {
    Write-Host ("[done {0:HH:mm:ss}] start_demo: FAIL: docker compose exit {1}, see {2}\compose.log (elapsed {3})" -f (Get-Date), $composeExit, $logs, (Elapsed))
    exit 1
}

$containers = 'flood-kafka-1', 'flood-kafka-2', 'flood-kafka-3', 'flood-postgres'
$deadline = (Get-Date).AddSeconds($TimeoutS)
while (-not (Test-Healthy $containers)) {
    if ((Get-Date) -gt $deadline) {
        Write-Host ("[done {0:HH:mm:ss}] start_demo: FAIL: containers not healthy after {1} s (elapsed {2})" -f (Get-Date), $TimeoutS, (Elapsed))
        exit 1
    }
    Start-Sleep -Seconds 3
}
$step++
Show-Step $step $total 'brokers and PostgreSQL healthy'

$notes = @{}
foreach ($s in $services) {
    $notes[$s.name] = Start-Service-Hidden $s.name $s.pattern $s.exe $s.args
    $step++
    Show-Step $step $total ("{0}: {1}" -f $s.name, $notes[$s.name])
}

# Started is not running: wait until each one has stayed up, and Faust has logged Ready, the console answers.
$deadline = (Get-Date).AddSeconds($TimeoutS)
do {
    $bad = @()
    foreach ($s in $services) {
        if ((Find-Service $s.pattern).Count -eq 0) { $bad += "$($s.name) exited"; continue }
        if ($s.name -like 'faust_*' -and $notes[$s.name] -eq 'started') {
            $log = Join-Path $logs "$($s.name).log"
            $err = Join-Path $logs "$($s.name).err"
            $text = ''
            foreach ($f in $log, $err) { if (Test-Path $f) { $text += (Get-Content $f -Raw -ErrorAction SilentlyContinue) } }
            if ($text -notmatch 'Worker\]: Ready') { $bad += "$($s.name) not Ready" }
        }
    }
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:8765/api/state' | Out-Null } catch { $bad += 'console not answering' }
    if ($bad.Count -eq 0) { break }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
$step++
Show-Step $step $total $(if ($bad.Count) { 'NOT READY: ' + ($bad -join ', ') } else { 'all running' })

$started = @($notes.Keys | Where-Object { $notes[$_] -eq 'started' }).Count
$status = if ($bad.Count) { 'FAIL: ' + ($bad -join ', ') } else { "PASS: $($services.Count) processes running ($started started, $($services.Count - $started) already running), 4 containers healthy" }
Write-Host ("[done {0:HH:mm:ss}] start_demo: {1} (elapsed {2})" -f (Get-Date), $status, (Elapsed))
if ($bad.Count) { Write-Host "  logs: $logs"; exit 1 }
Write-Host '  console  http://127.0.0.1:8765'
Write-Host '  replay   python -m producers.replay_producer --site 08158700 --start 2019-05-02 --end 2019-05-06 --speed 3600'
Write-Host '  check    python -m tools.verify_slice S1'
exit 0

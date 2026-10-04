param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$url = 'http://127.0.0.1:2233/workbench/'
foreach ($path in @('.secrets/env', '.secrets/bilibili.cookie', 'settings.toml', '.venv-win/Scripts/python.exe', '.venv-recorder/Scripts/python.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectDir $path))) { throw "Missing local setup: $path" }
}
function Test-LocalPort([int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try { $client.Connect('127.0.0.1', $Port); return $true }
    catch { return $false }
    finally { $client.Dispose() }
}
# Check every occupied port before starting anything. A running Dashboard
# alone is valid: fill in missing services rather than rejecting partial startup.
$entries = @(
    @{Name='start_windows_dashboard';Port=2234;Health='/api/health';Service='bilive-workbench'},
    @{Name='start_pipeline';Port=2235;Health='/health';Service='bilive-worker'},
    @{Name='start_windows_recorder';Port=2233;Health='/workbench/assets/app.js';Service=$null}
)
$missing = @()
foreach ($entry in $entries) {
    if (-not (Test-LocalPort $entry.Port)) { $missing += $entry; continue }
    $matches = $false
    try {
        $address = "http://127.0.0.1:$($entry.Port)$($entry.Health)"
        if ($entry.Service) {
            $health = Invoke-RestMethod $address -Proxy $null -TimeoutSec 3
            $matches = $health.service -eq $entry.Service
            if ($entry.Port -eq 2234 -and $matches) {
                $asset = Invoke-WebRequest 'http://127.0.0.1:2234/assets/app.js' -UseBasicParsing -Proxy $null -TimeoutSec 3
                $matches = $asset.Content -eq [IO.File]::ReadAllText((Join-Path $projectDir 'frontend/app.js'))
            }
        } else {
            # Recorder identity must not depend on the Dashboard being up.
            # Authenticate through the project's existing local secret loader.
            $env:PYTHONPATH = Join-Path $projectDir 'src'
            Push-Location -LiteralPath $projectDir
            try {
                $null = & (Join-Path $projectDir '.venv-win/Scripts/python.exe') -c "from pathlib import Path; from bilive.config.secrets import load_secret_env; from bilive.network import use_direct_network; from bilive.recorder.client import RecorderClient; load_secret_env(Path('.secrets/env')); use_direct_network(); RecorderClient().rooms()"
                $matches = $LASTEXITCODE -eq 0
            } finally { Pop-Location }
        }
    } catch { }
    if (-not $matches) { throw "Port $($entry.Port) belongs to an unavailable or different service. Existing processes were preserved." }
}
$logDir = Join-Path $projectDir 'logs/startup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $projectDir '.runtime') | Out-Null
$started = @()
foreach ($entry in $missing) {
    $name = $entry.Name
    $scriptPath = Join-Path $projectDir "$name.ps1"
    $process = Start-Process powershell.exe -WindowStyle Hidden -PassThru -WorkingDirectory $projectDir `
        -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$scriptPath+'"')) `
        -RedirectStandardOutput (Join-Path $logDir "$name.out.log") `
        -RedirectStandardError (Join-Path $logDir "$name.err.log")
    $started += @{entry=$name;pid=$process.Id}
}
if ($started.Count -gt 0) {
    $statePath = Join-Path $projectDir '.runtime/local-services.json'
    $previous = if (Test-Path -LiteralPath $statePath) { @(Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json) } else { @() }
    @($previous + $started) | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}
$ready = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:2234/api/health' -Proxy $null -TimeoutSec 1
        $worker = Invoke-RestMethod 'http://127.0.0.1:2235/health' -Proxy $null -TimeoutSec 1
        $rooms = Invoke-RestMethod 'http://127.0.0.1:2233/studio-api/rooms' -Proxy $null -TimeoutSec 2
        if ($health.service -eq 'bilive-workbench' -and $worker.service -eq 'bilive-worker' -and $null -ne $rooms.items) { $ready=$true;break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw "Startup not ready. Inspect logs/startup in $projectDir; started processes were preserved." }
Write-Output "Bilive ready: $url"
if (-not $NoBrowser) { Start-Process $url }

param([int]$Port = 2235)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$workerPython = Join-Path $projectRoot '.venv-win/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $workerPython)) {
    throw 'Worker environment missing: create .venv-win and install the project dependencies first.'
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'
Push-Location -LiteralPath $projectRoot
try { & $workerPython (Join-Path $projectRoot 'scripts/start_worker.py') --serve --port $Port }
finally { Pop-Location }

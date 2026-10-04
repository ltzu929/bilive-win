$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$WorkerPython = Join-Path $ProjectDir '.venv-win\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $WorkerPython)) { throw 'Run setup_windows_workbench_env.ps1 first.' }
$env:PYTHONPATH = Join-Path $ProjectDir 'src'
Push-Location -LiteralPath $ProjectDir
try { & $WorkerPython (Join-Path $ProjectDir 'scripts\recording_retention.py'); exit $LASTEXITCODE }
finally { Pop-Location }

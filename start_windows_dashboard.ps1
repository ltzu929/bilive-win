param([int]$Port = 2234)
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DashboardPython = Join-Path $ProjectDir ".venv-win\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $DashboardPython)) { throw "Run setup_windows_workbench_env.ps1 first." }
$env:PYTHONPATH = Join-Path $ProjectDir "src"
Push-Location -LiteralPath $ProjectDir
try { & $DashboardPython (Join-Path $ProjectDir "scripts\start_dashboard.py") --port $Port; exit $LASTEXITCODE }
finally { Pop-Location }

param([int]$Port = 2233, [string]$VideosDir = "", [string]$Settings = "")
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RecorderPython = Join-Path $ProjectDir ".venv-recorder\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $RecorderPython)) { throw "Run setup_windows_recorder_env.ps1 first." }
$env:PYTHONPATH = Join-Path $ProjectDir "src"
$arguments = @("-m", "bilive.recorder.server", "--port", $Port)
if ($VideosDir) { $arguments += @("--videos-dir", $VideosDir) }
if ($Settings) { $arguments += @("--settings", $Settings) }
Push-Location -LiteralPath $ProjectDir
try { & $RecorderPython @arguments; exit $LASTEXITCODE } finally { Pop-Location }

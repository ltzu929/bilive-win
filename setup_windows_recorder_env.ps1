param([string]$PythonVersion = "3.10")
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RecorderEnvDir = Join-Path $ProjectDir ".venv-recorder"
$RecorderPython = Join-Path $RecorderEnvDir "Scripts\python.exe"
Push-Location -LiteralPath $ProjectDir
try {
    if (-not (Test-Path -LiteralPath $RecorderPython)) {
        & uv venv --python $PythonVersion --seed $RecorderEnvDir
        if ($LASTEXITCODE -ne 0) { throw "Recorder environment creation failed." }
    }
    & $RecorderPython -m pip install --no-build-isolation --requirement (Join-Path $ProjectDir "requirements\recorder-windows.txt")
    if ($LASTEXITCODE -ne 0) { throw "Recorder dependencies failed." }
    & $RecorderPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Recorder dependency conflicts." }
    $SettingsPath = Join-Path $ProjectDir "settings.toml"
    if (-not (Test-Path -LiteralPath $SettingsPath)) {
        Copy-Item -LiteralPath (Join-Path $ProjectDir "settings.example.toml") -Destination $SettingsPath
    }
} finally { Pop-Location }

param([string]$PythonVersion = "3.13")
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkbenchEnvDir = Join-Path $ProjectDir ".venv-win"
$WorkbenchPython = Join-Path $WorkbenchEnvDir "Scripts\python.exe"
Push-Location -LiteralPath $ProjectDir
try {
    if (-not (Test-Path -LiteralPath $WorkbenchPython)) {
        & uv venv --python $PythonVersion --seed $WorkbenchEnvDir
        if ($LASTEXITCODE -ne 0) { throw "Workbench environment creation failed." }
    }
    & $WorkbenchPython -m pip install "setuptools>=68,<81" wheel hatchling
    if ($LASTEXITCODE -ne 0) { throw "Workbench build tools failed." }
    & $WorkbenchPython -m pip install --no-build-isolation -e ".[dev,pipeline]"
    if ($LASTEXITCODE -ne 0) { throw "Workbench dependencies failed." }
    & $WorkbenchPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Workbench dependency conflicts." }
} finally { Pop-Location }

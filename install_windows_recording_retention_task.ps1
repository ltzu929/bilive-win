param([string]$ProjectDir = $PSScriptRoot, [switch]$Install)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows_tasks.ps1')
$Plan = @(Get-BiliveTaskPlan -ProjectDir $ProjectDir -Kind retention)
if ($Install) { Register-BiliveTaskPlan -Plan $Plan }
else { $Plan | ConvertTo-Json -Depth 5 }

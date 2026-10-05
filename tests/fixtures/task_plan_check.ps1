param([string]$ProjectDir)
$ErrorActionPreference='Stop'
. (Join-Path $ProjectDir 'scripts\windows_tasks.ps1')
$script:Registered=@()
function Get-ScheduledTask { return $null }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory); return @{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User,[switch]$Daily,$At); return @{Logon=[bool]$AtLogOn;Daily=[bool]$Daily;At=$At} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances,[switch]$StartWhenAvailable,$ExecutionTimeLimit); return @{MultipleInstances=$MultipleInstances;CatchUp=[bool]$StartWhenAvailable;Limit=$ExecutionTimeLimit.TotalSeconds} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel); return @{User=$UserId;Type=$LogonType;Level=$RunLevel} }
function Register-ScheduledTask { param($TaskName,$TaskPath,$Action,$Trigger,$Settings,$Principal,[switch]$Force); $script:Registered+=@{Name=$TaskName;Path=$TaskPath;Action=$Action;Trigger=$Trigger;Settings=$Settings;Principal=$Principal} }
$Plan=@(Get-BiliveTaskPlan -ProjectDir $ProjectDir -Kind startup)+@(Get-BiliveTaskPlan -ProjectDir $ProjectDir -Kind retention)
Register-BiliveTaskPlan -Plan $Plan
if ($script:Registered.Count -ne 3) { throw 'wrong registration count' }
if ($script:Registered[2].Trigger.At -ne '08:30' -or -not $script:Registered[2].Settings.CatchUp) { throw 'retention schedule incorrect' }
foreach ($Item in $script:Registered) {
    $Windowless = $Item.Action.Execute -match 'pythonw.exe$' -and $Item.Action.Arguments -match 'run_windows_task.py'
    if ($Item.Principal.Type -ne 'Interactive' -or -not $Windowless -or $Item.Settings.MultipleInstances -ne 'IgnoreNew') { throw 'incorrect task boundary' }
}
$script:Updated=@()
function Get-ScheduledTask { param($TaskName,$TaskPath); $Item=$Plan | Where-Object TaskName -eq $TaskName; return @{State='Running';Actions=@($Item.LegacyAction)} }
function Set-ScheduledTask { param($TaskName,$TaskPath,$Action,$Trigger); $script:Updated+=@{Name=$TaskName;Action=$Action;Trigger=$Trigger} }
$script:Registered=@()
Register-BiliveTaskPlan -Plan @($Plan[0],$Plan[1])
if ($script:Updated.Count -ne 2 -or $script:Registered.Count -ne 0) { throw 'legacy running tasks must only update actions' }
function Get-ScheduledTask { param($TaskName,$TaskPath); $Item=$Plan | Where-Object TaskName -eq $TaskName; return @{State='Running';Actions=@(@{Execute=$Item.Execute;Arguments=$Item.Arguments;WorkingDirectory=$Item.WorkingDirectory})} }
$script:Updated=@()
Register-BiliveTaskPlan -Plan @($Plan[0],$Plan[1])
if ($script:Updated.Count -ne 0 -or $script:Registered.Count -ne 0) { throw 'matching running tasks must be left intact' }
Register-BiliveTaskPlan -Plan @($Plan[2])
if ($script:Updated.Count -ne 1 -or $script:Updated[0].Trigger.At -ne '08:30' -or $script:Updated[0].Action) {
    throw 'existing retention schedule must update without replacing its action'
}
$script:Registered=@()
function Get-ScheduledTask { return @{State='Ready';Actions=@(@{Execute='unrelated';Arguments='elsewhere';WorkingDirectory='other'})} }
try { Register-BiliveTaskPlan -Plan $Plan;throw 'unrelated task should be rejected' }
catch { if ($_.Exception.Message -notmatch 'different entry') { throw } }
if ($script:Registered.Count -ne 0) { throw 'partial writes before target verification' }
Write-Output 'TASK_PLAN_PASS: windowless login, retention catchup, legacy running actions updated, matching tasks preserved, unrelated targets refused'

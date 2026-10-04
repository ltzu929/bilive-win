# Shared plan/registration for the independent Recorder, Dashboard and retention.
function Get-BiliveTaskPlan {
    param([string]$ProjectDir, [ValidateSet('startup','retention')][string]$Kind)
    $ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
    $ShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Entries = if ($Kind -eq 'startup') {
        @(@{Name='BiliveV2Recorder';Script='start_windows_recorder.ps1';Venv='.venv-recorder';Trigger='Logon'},
          @{Name='BiliveV2Dashboard';Script='start_windows_dashboard.ps1';Venv='.venv-win';Trigger='Logon'})
    } else {
        @(@{Name='BiliveV2Retention';Script='start_recording_retention.ps1';Venv='.venv-win';Trigger='Daily';At='03:00'})
    }
    foreach ($Entry in $Entries) {
        $ScriptPath = Join-Path $ProjectDir $Entry.Script
        $PythonExe = Join-Path $ProjectDir ($Entry.Venv + '\Scripts\python.exe')
        if (-not (Test-Path -LiteralPath $ScriptPath) -or -not (Test-Path -LiteralPath $PythonExe)) {
            throw "Entry/environment missing for $($Entry.Name); run environment setup first."
        }
        $Execute = $ShellExe
        $Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""
        $LegacyAction = $null
        if ($Kind -eq 'startup') {
            $Execute = Join-Path $ProjectDir ($Entry.Venv + '\Scripts\pythonw.exe')
            $Launcher = Join-Path $ProjectDir 'scripts\run_windows_task.py'
            if (-not (Test-Path -LiteralPath $Execute) -or -not (Test-Path -LiteralPath $Launcher)) {
                throw "Windowless launcher missing for $($Entry.Name)."
            }
            $LegacyAction = [pscustomobject]@{Execute=$ShellExe;Arguments=$Arguments;WorkingDirectory=$ProjectDir}
            $Arguments = "`"$Launcher`" `"$ScriptPath`""
        }
        [pscustomobject]@{TaskName=$Entry.Name;TaskPath='\BiliveV2\';Execute=$Execute;
            Arguments=$Arguments;LegacyAction=$LegacyAction;
            WorkingDirectory=$ProjectDir;Trigger=$Entry.Trigger;At=$Entry.At;
            StartWhenAvailable=$true;MultipleInstances='IgnoreNew';ExecutionTimeLimitSeconds=0}
    }
}

function Register-BiliveTaskPlan {
    param([object[]]$Plan)
    $UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $ExistingTasks = @{}
    # Inspect every target before making any change. Never stop an existing service.
    foreach ($Item in $Plan) {
        $Existing = Get-ScheduledTask -TaskName $Item.TaskName -TaskPath $Item.TaskPath -ErrorAction SilentlyContinue
        if ($Existing) {
            $Matches = @($Existing.Actions).Count -eq 1 -and $Existing.Actions[0].Execute -eq $Item.Execute -and
                $Existing.Actions[0].Arguments -eq $Item.Arguments -and $Existing.Actions[0].WorkingDirectory -eq $Item.WorkingDirectory
            $LegacyMatches = $Item.LegacyAction -and @($Existing.Actions).Count -eq 1 -and
                $Existing.Actions[0].Execute -eq $Item.LegacyAction.Execute -and
                $Existing.Actions[0].Arguments -eq $Item.LegacyAction.Arguments -and
                $Existing.Actions[0].WorkingDirectory -eq $Item.LegacyAction.WorkingDirectory
            if (-not $Matches -and -not $LegacyMatches) {
                throw "Task $($Item.TaskName) belongs to a different entry; refusing to replace it."
            }
            $ExistingTasks[$Item.TaskName] = @{Matches=$Matches}
        }
    }
    foreach ($Item in $Plan) {
        $Action = New-ScheduledTaskAction -Execute $Item.Execute -Argument $Item.Arguments -WorkingDirectory $Item.WorkingDirectory
        if ($ExistingTasks.ContainsKey($Item.TaskName)) {
            $Saved = $ExistingTasks[$Item.TaskName]
            # Updating a known legacy action affects future launches only.
            # Keep the current service, triggers, settings and principal intact.
            if (-not $Saved.Matches) {
                Set-ScheduledTask -TaskName $Item.TaskName -TaskPath $Item.TaskPath -Action $Action
            }
            continue
        }
        $Trigger = if ($Item.Trigger -eq 'Logon') { New-ScheduledTaskTrigger -AtLogOn -User $UserId }
                   else { New-ScheduledTaskTrigger -Daily -At $Item.At }
        $Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
        $Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $Item.TaskName -TaskPath $Item.TaskPath -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force
    }
}

$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$taskName = 'ProDog-Gmail-Local'
$entryPoint = Join-Path $taskRoot 'scripts\run_background.py'
$python = Join-Path $taskRoot '.venv\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment is missing. Run .\start.ps1 once to install dependencies.'
}

$listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    foreach ($listener in $listeners) {
        $serverProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listener.OwningProcess)
        if ($serverProcess -and $serverProcess.CommandLine -and $serverProcess.CommandLine.Contains($taskRoot) -and ($serverProcess.CommandLine.Contains('prodog_gmail.app:app') -or $serverProcess.CommandLine.Contains($entryPoint))) {
            Write-Host 'ProDog is already running at http://127.0.0.1:8765.'
            return
        }
    }
    throw 'Port 8765 is used by another process. Nothing was changed.'
}

$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $entryPoint + '"') -WorkingDirectory $taskRoot
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and ($existing.Actions.Execute -ne $python -or -not $existing.Actions.Arguments.Contains($entryPoint))) {
    throw 'A different scheduled task already uses this name. Nothing was changed.'
}
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Description 'Run the local ProDog Gmail prototype on demand.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $serverProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listener.OwningProcess)
        if ($serverProcess -and $serverProcess.CommandLine -and $serverProcess.CommandLine.Contains($taskRoot)) {
            Write-Host 'ProDog is running at http://127.0.0.1:8765.'
            return
        }
        throw 'Port 8765 became occupied by another process.'
    }
}
throw 'The server did not start. Check artifacts\server.background.log.'

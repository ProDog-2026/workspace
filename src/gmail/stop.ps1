$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$task = Get-ScheduledTask -TaskName 'ProDog-Gmail-Local' -ErrorAction SilentlyContinue
if ($task -and $task.Actions.Execute -eq (Join-Path $taskRoot '.venv\Scripts\pythonw.exe') -and $task.Actions.Arguments.Contains((Join-Path $taskRoot 'scripts\run_background.py'))) {
    Stop-ScheduledTask -TaskName 'ProDog-Gmail-Local' -ErrorAction SilentlyContinue
}
$listeners = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $serverProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listener.OwningProcess)
    if ($serverProcess -and $serverProcess.CommandLine -and $serverProcess.CommandLine.Contains($taskRoot) -and ($serverProcess.CommandLine.Contains('prodog_gmail.app:app') -or $serverProcess.CommandLine.Contains((Join-Path $taskRoot 'scripts\run_background.py')))) {
        Stop-Process -Id $listener.OwningProcess
        Write-Host 'Stopped the ProDog local server.'
    } else {
        Write-Host 'Port 8765 is used by a different process. Left it running.'
    }
}

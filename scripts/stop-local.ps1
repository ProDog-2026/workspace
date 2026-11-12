param([switch]$Preview, [switch]$NoRun)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$gmailDir = Join-Path $root 'src\gmail'
$dashboardDir = Join-Path $root 'src\dashboard'
$agentDir = Join-Path $root 'src\agent-container'
$script:failures = New-Object System.Collections.Generic.List[string]

function Contains-Path([string]$text, [string]$path) {
    if (-not $text) { return $false }
    return $text.Replace('/', '\').IndexOf($path.Replace('/', '\'), [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Find-ProjectProcess($process, [string]$kind, $all) {
    if (-not $process -or -not $process.CommandLine) { return $null }
    $line = [string]$process.CommandLine
    if ($kind -eq 'dashboard') {
        if ($process.Name -ieq 'node.exe' -and (Contains-Path $line (Join-Path $dashboardDir 'node_modules')) -and $line -match '(?i)vite') {
            return @{ Listener = $process; Launcher = $null }
        }
        return $null
    }
    if ($process.Name -notmatch '(?i)^pythonw?\.exe$' -and $process.Name -notmatch '(?i)^uvicorn\.exe$') { return $null }
    if ($line -notmatch '(?i)prodog_gmail\.app:app' -and -not (Contains-Path $line (Join-Path $gmailDir 'scripts\run_background.py'))) { return $null }
    $current = $process
    for ($i = 0; $i -lt 5 -and $current; $i++) {
        if ((Contains-Path ([string]$current.ExecutablePath) (Join-Path $gmailDir '.venv\Scripts')) -or
            (Contains-Path ([string]$current.CommandLine) (Join-Path $gmailDir '.venv\Scripts')) -or
            (Contains-Path ([string]$current.CommandLine) (Join-Path $gmailDir 'scripts\run_background.py'))) {
            $launcher = if ($current.ProcessId -ne $process.ProcessId) { $current } else { $null }
            return @{ Listener = $process; Launcher = $launcher }
        }
        $parentId = $current.ParentProcessId
        $current = $all | Where-Object { $_.ProcessId -eq $parentId } | Select-Object -First 1
    }
    return $null
}

function Stop-VerifiedProcess($process) {
    if (-not $process) { return }
    if ($Preview) { Write-Host "  종료 예정 PID: $($process.ProcessId)"; return }
    $current = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $process.ProcessId) -ErrorAction SilentlyContinue
    if (-not $current) { return }
    if ($current.CreationDate -ne $process.CreationDate -or $current.CommandLine -ne $process.CommandLine) {
        throw "PID $($process.ProcessId)의 프로세스가 바뀌어 종료하지 않았습니다."
    }
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
}

function Stop-Port([int]$port, [string]$kind, [string]$label) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { Write-Host "$label : 이미 종료됨"; return }
    $all = @(Get-CimInstance Win32_Process)
    $processed = @{}
    foreach ($listener in $listeners) {
        if ($processed.ContainsKey([int]$listener.OwningProcess)) { continue }
        $processed[[int]$listener.OwningProcess] = $true
        $process = $all | Where-Object { $_.ProcessId -eq $listener.OwningProcess } | Select-Object -First 1
        $match = Find-ProjectProcess $process $kind $all
        if (-not $match) {
            Write-Host "$label : $port 포트의 다른 프로세스는 그대로 둡니다."
            continue
        }
        Stop-VerifiedProcess $match.Listener
        Stop-VerifiedProcess $match.Launcher
        if (-not $Preview) { Write-Host "$label : 종료 완료" }
    }
}

function Stop-GmailTask {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { return }
    $task = Get-ScheduledTask -TaskName 'ProDog-Gmail-Local' -ErrorAction SilentlyContinue
    $expectedExe = Join-Path $gmailDir '.venv\Scripts\pythonw.exe'
    $expectedScript = Join-Path $gmailDir 'scripts\run_background.py'
    if ($task -and $task.State -eq 'Running' -and @($task.Actions | Where-Object {
        $_.Execute -ieq $expectedExe -and (Contains-Path $_.Arguments $expectedScript)
    }).Count -gt 0) {
        if ($Preview) { Write-Host '  Gmail 예약 작업 종료 예정'; return }
        Stop-ScheduledTask -TaskName 'ProDog-Gmail-Local' -ErrorAction Stop
        Write-Host 'Gmail 예약 작업을 종료했습니다.'
    }
}

function Confirm-PortStopped([int]$port, [string]$kind) {
    if ($Preview) { return }
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -eq 0) { return }
        $all = @(Get-CimInstance Win32_Process)
        $projectListeners = @($listeners | Where-Object {
            $ownerId = $_.OwningProcess
            $process = $all | Where-Object { $_.ProcessId -eq $ownerId } | Select-Object -First 1
            Find-ProjectProcess $process $kind $all
        })
        if ($projectListeners.Count -eq 0) { return }
        Start-Sleep -Milliseconds 300
    }
    throw "프로젝트 서버가 $port 포트에서 아직 실행 중입니다."
}

function Invoke-DockerQuiet([string[]]$Arguments) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& docker @Arguments 2>$null)
        return @{ Code = $LASTEXITCODE; Output = $output }
    } finally { $ErrorActionPreference = $previous }
}

function Stop-Agent {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker를 찾지 못해 컨테이너 상태를 확인할 수 없습니다.' }
    $info = Invoke-DockerQuiet @('info', '--format', '{{.ServerVersion}}')
    if ($info.Code -ne 0) { throw 'Docker 엔진에 연결할 수 없어 컨테이너 상태를 확인할 수 없습니다.' }
    $containers = Invoke-DockerQuiet @('ps', '-a', '--filter', 'label=com.docker.compose.project=agent-container', '-q')
    if ($containers.Code -ne 0) { throw '동적 검사 컨테이너 목록을 확인하지 못했습니다.' }
    if ($containers.Output.Count -eq 0) {
        $orphaned = Invoke-DockerQuiet @('ps', '--filter', 'label=agent.run_id', '--filter', 'network=work-net', '-q')
        if ($orphaned.Code -ne 0 -or $orphaned.Output.Count -gt 0) {
            throw '남아 있는 검사 컨테이너의 소유자를 확인할 수 없습니다. Docker에서 직접 확인하세요.'
        }
        Write-Host '동적 검사 컨테이너: 이미 종료됨'
        return
    }
    $expected = [IO.Path]::GetFullPath($agentDir).TrimEnd('\')
    foreach ($containerId in $containers.Output) {
        $inspection = Invoke-DockerQuiet @('inspect', $containerId)
        if ($inspection.Code -ne 0) { throw '컨테이너의 프로젝트 경로를 확인하지 못했습니다.' }
        $details = @(($inspection.Output -join "`n") | ConvertFrom-Json)
        $directory = [string]$details[0].Config.Labels.'com.docker.compose.project.working_dir'
        if (-not $directory -or -not $directory.TrimEnd('\').Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw '같은 이름의 다른 Compose 프로젝트가 있어 컨테이너를 건드리지 않았습니다.'
        }
    }
    $known = @{}
    try {
        $runs = @(Invoke-RestMethod 'http://127.0.0.1:8000/runs' -TimeoutSec 5)
        foreach ($run in $runs) {
            if ($run.run_id -match '^[A-Za-z0-9_-]{1,128}$') {
                $known[[string]$run.run_id] = $true
                if (-not $Preview -and $run.status -eq 'running') {
                    try { Invoke-RestMethod -Method Post -Uri ("http://127.0.0.1:8000/runs/" + $run.run_id + '/stop') -TimeoutSec 5 | Out-Null }
                    catch { Write-Host '진행 중인 검사에 중지 요청을 보내지 못했습니다. 컨테이너 종료를 계속합니다.' }
                }
            }
        }
    } catch { Write-Host '실행기 API를 사용할 수 없어 기존 검사 기록은 확인하지 못했습니다.' }
    if ($Preview) { Write-Host "동적 검사 컨테이너: 프로젝트 $($containers.Output.Count)개 종료 예정"; return }
    Push-Location -LiteralPath $agentDir
    try {
        $result = Invoke-DockerQuiet @('compose', '--project-name', 'agent-container', 'stop', '--timeout', '30')
        if ($result.Code -ne 0) { throw '동적 검사 Compose 서비스를 종료하지 못했습니다.' }
    } finally { Pop-Location }
    # The runtime creates temporary worker containers outside Compose. Stop only
    # those on this stack's network whose run IDs were confirmed by its API.
    $workers = Invoke-DockerQuiet @('ps', '--filter', 'label=agent.run_id', '--filter', 'network=work-net', '-q')
    if ($workers.Code -ne 0) { throw '남은 검사 컨테이너를 확인하지 못했습니다.' }
    $unknownWorkers = 0
    foreach ($worker in $workers.Output) {
        $inspection = Invoke-DockerQuiet @('inspect', $worker)
        $details = @(if ($inspection.Code -eq 0) { ($inspection.Output -join "`n") | ConvertFrom-Json })
        $runId = if ($details.Count -eq 1) { [string]$details[0].Config.Labels.'agent.run_id' } else { '' }
        if ($runId -and $known.ContainsKey($runId)) {
            $stopped = Invoke-DockerQuiet @('stop', '--time', '10', $worker)
            if ($stopped.Code -ne 0) { throw '검사 작업 컨테이너를 종료하지 못했습니다.' }
        } else {
            $unknownWorkers++
        }
    }
    $remaining = Invoke-DockerQuiet @('ps', '--filter', 'label=com.docker.compose.project=agent-container', '-q')
    if ($remaining.Code -ne 0 -or $remaining.Output.Count -gt 0) { throw '일부 Compose 서비스가 아직 실행 중입니다.' }
    if ($unknownWorkers -gt 0) { throw '소유자를 확인할 수 없는 작업 컨테이너는 그대로 두었습니다. Docker에서 직접 확인하세요.' }
    Write-Host '동적 검사 컨테이너: 종료 완료'
}

if ($NoRun) { return }
Write-Host 'ProDog 종료 대상을 확인합니다...'
foreach ($step in @(
    @{ Label = '웹 대시보드'; Action = { Stop-Port 5173 'dashboard' '웹 대시보드'; Confirm-PortStopped 5173 'dashboard' } },
    @{ Label = 'Gmail 백엔드'; Action = { Stop-Port 8765 'gmail' 'Gmail 백엔드'; Stop-GmailTask; Confirm-PortStopped 8765 'gmail' } },
    @{ Label = '동적 검사 컨테이너'; Action = { Stop-Agent } }
)) {
    try { & $step.Action } catch { $script:failures.Add("$($step.Label): $($_.Exception.Message)"); Write-Host $script:failures[$script:failures.Count - 1] -ForegroundColor Red }
}
if ($script:failures.Count -gt 0) { Write-Host '일부 종료 상태를 확인하지 못했습니다.'; exit 1 }
if ($Preview) { Write-Host '미리보기 완료: 실행 중인 서비스는 변경하지 않았습니다.' }
else { Write-Host 'ProDog 종료 완료. Docker Desktop과 Ollama는 계속 실행됩니다.' }

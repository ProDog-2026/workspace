param([switch]$NoBrowser, [switch]$NoRun)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$gmailDir = Join-Path $root 'src\gmail'
$dashboardDir = Join-Path $root 'src\dashboard'
$agentDir = Join-Path $root 'src\agent-container'
$python = Join-Path $gmailDir '.venv\Scripts\python.exe'
$dashboardUrl = 'http://127.0.0.1:5173/'
$gmailUrl = 'http://127.0.0.1:8765'
$agentUrl = 'http://127.0.0.1:8000'

function Test-Port([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(800)) { return $false }
        $client.EndConnect($pending)
        return $true
    } catch { return $false } finally { $client.Dispose() }
}

function Test-Gmail {
    try {
        $session = Invoke-RestMethod "$gmailUrl/api/session" -TimeoutSec 3
        return $session.mode -in @('google', 'demo') -and $null -ne $session.configured
    } catch { return $false }
}

function Test-Dashboard {
    try {
        $page = Invoke-WebRequest $dashboardUrl -UseBasicParsing -TimeoutSec 3
        return $page.StatusCode -eq 200 -and $page.Content.Contains('<title>ProDog</title>')
    } catch { return $false }
}

function Test-Control {
    try {
        $response = Invoke-RestMethod "$agentUrl/prompts" -TimeoutSec 3
        return $null -ne $response.prompts
    } catch { return $false }
}

function Wait-Ready([scriptblock]$probe, [int]$attempts) {
    for ($i = 0; $i -lt $attempts; $i++) {
        if (& $probe) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# Windows PowerShell 5.1 can turn native stderr into a terminating error when
# ErrorActionPreference is Stop, including normal Docker "image not found".
function Invoke-DockerQuiet([string[]]$Arguments) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& docker @Arguments 2>$null)
        return @{ Code = $LASTEXITCODE; Output = $output }
    } finally { $ErrorActionPreference = $previous }
}

function Invoke-Native([string]$Program, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Program @Arguments | Out-Host
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

function Start-DockerEngine {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker 명령을 찾을 수 없습니다. Docker Desktop을 설치한 뒤 다시 실행하세요.'
    }
    $info = Invoke-DockerQuiet @('info', '--format', '{{.ServerVersion}}')
    if ($info.Code -eq 0) { return }
    $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path -LiteralPath $dockerDesktop) {
        Write-Host 'Docker Desktop을 시작하고 있습니다...'
        Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep -Seconds 1
            $info = Invoke-DockerQuiet @('info', '--format', '{{.ServerVersion}}')
            if ($info.Code -eq 0) { return }
        }
    }
    throw 'Docker 엔진에 연결할 수 없습니다. Docker Desktop의 Linux 컨테이너 모드를 확인하세요.'
}

function Start-Agent {
    Start-DockerEngine
    Push-Location -LiteralPath $agentDir
    try {
        $imagesReady = $true
        foreach ($image in @('agent-container-control', 'agent-container-receiver', 'agent-container-worker:phase1')) {
            $inspection = Invoke-DockerQuiet @('image', 'inspect', $image, '--format', '{{.Id}}')
            if ($inspection.Code -ne 0) { $imagesReady = $false }
        }
        $controlState = Invoke-DockerQuiet @('inspect', '--format', '{{.State.Status}}', 'agent-container-control-1')
        $controlRunning = $controlState.Code -eq 0 -and $controlState.Output -contains 'running'
        $receiverHealth = Invoke-DockerQuiet @('inspect', '--format', '{{.State.Health.Status}}', 'agent-test-receiver')
        $receiverReady = $receiverHealth.Code -eq 0 -and $receiverHealth.Output -contains 'healthy'
        if ($imagesReady -and $controlRunning -and $receiverReady -and (Test-Control)) {
            Write-Host '동적 검사 컨테이너가 이미 실행 중입니다.'
            return
        }
        $workers = Invoke-DockerQuiet @('ps', '--filter', 'label=agent.run_id', '--filter', 'status=running', '-q')
        if ($workers.Code -ne 0) { throw 'Docker 컨테이너 상태를 확인하지 못했습니다.' }
        if ($workers.Output.Count -gt 0) { throw '동적 검사가 실행 중입니다. 검사가 끝난 뒤 다시 실행하세요.' }
        if (Test-Control) {
            try { $runs = @(Invoke-RestMethod "$agentUrl/runs" -TimeoutSec 5) }
            catch { throw '동적 검사 실행 상태를 확인하지 못했습니다. 검사가 끝난 뒤 다시 시도하세요.' }
            $unfinished = @($runs | Where-Object { $_.phase -ne 'finished' -and -not ($_.phase -eq 'manual' -and $_.status -ne 'running') })
            if ($unfinished.Count -gt 0) { throw '동적 검사가 진행 중이거나 확인되지 않았습니다. 완료 후 다시 실행하세요.' }
        } elseif ((Test-Port 8000) -and -not $controlRunning) {
            throw '8000번 포트를 다른 프로그램이 사용하고 있습니다. 확인 후 다시 실행하세요.'
        }
        Write-Host '동적 검사 이미지를 준비하고 컨테이너를 실행합니다. 처음에는 몇 분 걸릴 수 있습니다...'
        $exitCode = Invoke-Native 'docker' @('compose', 'up', '-d', '--build')
        if ($exitCode -ne 0) { throw '동적 검사 컨테이너 실행에 실패했습니다. Docker 메시지를 확인하세요.' }
        if (-not (Wait-Ready { Test-Control } 30)) { throw '동적 검사 서버에 연결하지 못했습니다.' }
        Write-Host '동적 검사 서버가 준비됐습니다.'
    } finally { Pop-Location }
}

function Start-Gmail {
    if (Test-Gmail) { Write-Host 'Gmail 백엔드가 이미 실행 중입니다.'; return }
    if (Test-Port 8765) { throw '8765번 포트를 다른 프로그램이 사용하고 있습니다.' }
    if (-not (Test-Path -LiteralPath $python)) {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv를 설치한 뒤 다시 실행하세요.' }
        Write-Host 'Gmail 백엔드 의존성을 설치합니다...'
        Push-Location -LiteralPath $gmailDir
        try {
            $exitCode = Invoke-Native 'uv' @('sync', '--frozen')
            if ($exitCode -ne 0) { throw 'Gmail 백엔드 의존성 설치에 실패했습니다.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $gmailDir '.env'))) {
        & $python (Join-Path $gmailDir 'scripts\setup_local.py')
        if ($LASTEXITCODE -ne 0) { throw 'Gmail 초기 설정에 실패했습니다.' }
    }
    $logDir = Join-Path $gmailDir 'artifacts'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList @('-m', 'uvicorn', 'prodog_gmail.app:app', '--host', '127.0.0.1', '--port', '8765', '--no-access-log') -WorkingDirectory $gmailDir -RedirectStandardOutput (Join-Path $logDir 'run-backend.out.log') -RedirectStandardError (Join-Path $logDir 'run-backend.err.log') | Out-Null
    if (-not (Wait-Ready { Test-Gmail } 30)) { throw "Gmail 백엔드를 시작하지 못했습니다. 로그: $logDir\run-backend.err.log" }
    Write-Host 'Gmail 백엔드가 준비됐습니다.'
}

function Start-Dashboard {
    if (Test-Dashboard) { Write-Host '웹 대시보드가 이미 실행 중입니다.'; return }
    if (Test-Port 5173) { throw '5173번 포트를 다른 프로그램이 사용하고 있습니다.' }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js와 npm을 설치한 뒤 다시 실행하세요.' }
    if (-not (Test-Path -LiteralPath (Join-Path $dashboardDir 'node_modules\vite\bin\vite.js'))) {
        Write-Host '웹 대시보드 의존성을 설치합니다...'
        Push-Location -LiteralPath $dashboardDir
        try {
            $exitCode = Invoke-Native 'npm.cmd' @('install')
            if ($exitCode -ne 0) { throw '웹 대시보드 의존성 설치에 실패했습니다.' }
        } finally { Pop-Location }
    }
    $logDir = Join-Path $dashboardDir 'artifacts'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Process -WindowStyle Hidden -FilePath 'npm.cmd' -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1') -WorkingDirectory $dashboardDir -RedirectStandardOutput (Join-Path $logDir 'run-dashboard.out.log') -RedirectStandardError (Join-Path $logDir 'run-dashboard.err.log') | Out-Null
    if (-not (Wait-Ready { Test-Dashboard } 30)) { throw "웹 대시보드를 시작하지 못했습니다. 로그: $logDir\run-dashboard.err.log" }
    Write-Host '웹 대시보드가 준비됐습니다.'
}

if ($NoRun) { return }

try {
    Write-Host 'ProDog 실행 환경을 확인합니다...'
    Start-Agent
    Start-Gmail
    Start-Dashboard
    Write-Host "`n실행 완료: $dashboardUrl"
    if (-not $NoBrowser) {
        try { Start-Process -WindowStyle Hidden -FilePath $dashboardUrl | Out-Null }
        catch { Write-Host "브라우저에서 직접 열어 주세요: $dashboardUrl" }
    }
} catch {
    Write-Host "`n실행 실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

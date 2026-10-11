$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    uv run python scripts/setup_local.py
    if ($LASTEXITCODE -ne 0) { throw 'Local configuration failed.' }
    Write-Host 'Open http://127.0.0.1:8765 in your browser. Ctrl+C stops the server.'
    uv run uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
} finally {
    Pop-Location
}

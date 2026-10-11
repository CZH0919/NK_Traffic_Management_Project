param([ValidateRange(1024,65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot 'Traffic_Venv\Scripts\python.exe'
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython scripts/run_local.py --port $Port
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

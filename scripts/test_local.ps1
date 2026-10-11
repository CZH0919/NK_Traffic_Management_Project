$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot 'Traffic_Venv\Scripts\python.exe'
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython -m pytest --reuse-db @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

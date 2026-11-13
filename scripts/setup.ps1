$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 environment setup failed.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -e './agent_service[test]'
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies failed.' }
Push-Location 'agent_service/frontend'
try {
    if (Test-Path 'package-lock.json') { npm ci } else { npm install }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Output 'Setup complete. Daily use: double-click Start.cmd. No reinstall needed.'

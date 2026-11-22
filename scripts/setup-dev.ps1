. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $DevPython)) {
    & python -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ must be on PATH.' }
    & python -m venv (Join-Path $RepoRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
}
Invoke-DevPython -m pip install -r requirements.txt
Set-DevEnvironment
Invoke-DevPython -m server.cli migrate

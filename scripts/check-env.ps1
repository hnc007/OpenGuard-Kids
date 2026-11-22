. "$PSScriptRoot\common.ps1"
"Workspace: $RepoRoot"
Get-Command python,git -ErrorAction SilentlyContinue | Select-Object Name,Source
if (Test-Path -LiteralPath $DevPython) {
    Invoke-DevPython --version
    Invoke-DevPython -m pip check
    Invoke-DevPython -c 'import fastapi, sqlalchemy, dns, win32crypt'
    Write-Output 'Imports OK'
}

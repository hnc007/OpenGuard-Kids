. "$PSScriptRoot\common.ps1"
$env:PYTHONUTF8 = '1'
Invoke-DevPython -m pytest @args

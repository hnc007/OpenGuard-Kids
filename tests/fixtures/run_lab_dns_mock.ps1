# Isolated subprocess for each operation. No real network modules or executable on PATH.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Action, [Parameter(Mandatory)][string]$Confirmation)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Management, Microsoft.PowerShell.Utility -ErrorAction Stop
$PSModuleAutoLoadingPreference = 'None'
$env:PATH = ''
$env:COMPUTERNAME = 'OGK-TEST-VM'
$statePath = Join-Path $PSScriptRoot 'adapter.json'
$target = Join-Path $PSScriptRoot 'repo/scripts/lab/dns-adapter.ps1'
$global:LabAdapterFixture = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
function Read-Host([string]$Prompt) { $Confirmation }

# Fail before running the production control flow if a future edit introduces
# a call outside this small filesystem/inspection and fake-adapter interface.
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($target, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Unexpected production script parse error.' }
$allowed = @('Join-Path','Split-Path','Where-Object','Out-Host','Get-Content','ConvertFrom-Json','ConvertTo-Json',
             'Rename-Item','Write-Host','Read-Host','Test-Path','Get-LabAdapter','Get-LabRegistration',
             'Get-LabServers','Test-LabAdministrator','Test-LabProxy','Invoke-LabNetsh','Read-IPv4Config','Set-IPv4Config')
foreach ($call in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true)) {
    if ($call.GetCommandName() -in $allowed) { continue }
    if ($call.Extent.Text -ceq '. (Join-Path $PSScriptRoot ''dns-adapter-windows.ps1'')') { continue }
    throw "Unmocked command in production control flow: $($call.Extent.Text)"
}
$rc = 0
try {
    $preview = & $target -InterfaceIndex $global:LabAdapterFixture.index -Action $Action
    if ($Action -eq 'Preview') { $preview | ConvertTo-Json }
} catch {
    $rc = 1
    [Console]::Error.WriteLine($_.Exception.Message)
} finally {
    [IO.File]::WriteAllText($statePath, ($global:LabAdapterFixture | ConvertTo-Json -Depth 5))
}
exit $rc

# Windows boundary for dns-adapter.ps1. The caller supplies an interface index.
function Get-LabAdapter([int]$Index) {
    Get-NetAdapter -InterfaceIndex $Index -ErrorAction Stop
}

function Get-LabRegistration([int]$Index) {
    Get-DnsClient -InterfaceIndex $Index -ErrorAction Stop
}

function Get-LabServers([int]$Index, [string]$Family) {
    (Get-DnsClientServerAddress -InterfaceIndex $Index -AddressFamily $Family -ErrorAction Stop).ServerAddresses
}

function Test-LabAdministrator {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-LabProxy {
    $udp = Get-NetUDPEndpoint -LocalAddress '127.0.0.1' -LocalPort 53 -ErrorAction SilentlyContinue
    $tcp = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 53 -State Listen -ErrorAction SilentlyContinue
    [bool]$udp -and [bool]$tcp
}

function Invoke-LabNetsh([string[]]$Arguments) {
    $netsh = Join-Path ([Environment]::SystemDirectory) 'netsh.exe'
    & $netsh @Arguments
    if ($LASTEXITCODE -ne 0) { throw "IPv4 netsh command failed (exit $LASTEXITCODE)." }
}

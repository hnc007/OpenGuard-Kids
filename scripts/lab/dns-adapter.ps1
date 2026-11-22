#requires -Version 5.1
<#
.SYNOPSIS
  Inspect, apply or restore the IPv4 loopback DNS setting on one Windows lab VM adapter.
  Run locally inside the VM. Preview is read-only. Apply and Restore require elevation.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateRange(1, 2147483647)][int]$InterfaceIndex,
    [ValidateSet('Preview','Apply','Restore')][string]$Action = 'Preview'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dns-adapter-windows.ps1')
$adapter = Get-LabAdapter -Index $InterfaceIndex
$guid = ([guid]$adapter.InterfaceGuid).ToString()
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backupDir = Join-Path $repo '.local/lab-dns'
$backupPath = Join-Path $backupDir "$guid.json"

function Read-IPv4Config {
    # netsh dump emits reusable configuration commands. Never execute the dump.
    $dump = Invoke-LabNetsh -Arguments @('interface','ipv4','dump')
    $name = [regex]::Escape($adapter.Name)
    $matchLines = @($dump | Where-Object { $_ -match "^\s*set\s+dnsservers\s+name=(?:`"$name`"|$name|$InterfaceIndex)\s+" })
    if ($matchLines.Count -ne 1 -or $matchLines[0] -notmatch '\bsource=(dhcp|static)\b') {
        throw 'Cannot reliably distinguish automatic and manual DNS on this Windows build. No changes applied.'
    }
    $source = $Matches[1].ToLowerInvariant()
    $dnsClient = Get-LabRegistration -Index $InterfaceIndex
    $register = if (!$dnsClient.RegisterThisConnectionsAddress) { 'none' }
                elseif ($dnsClient.UseSuffixWhenRegistering) { 'both' } else { 'primary' }
    $servers = @(Get-LabServers -Index $InterfaceIndex -Family IPv4 | Where-Object { $_ })
    if ($source -eq 'static' -and $servers.Count -eq 0 -and $matchLines[0] -notmatch '\baddress=none\b') {
        throw 'Manual server list is ambiguous. No changes applied.'
    }
    [pscustomobject]@{source=$source; servers=$servers; register=$register}
}

function Set-IPv4Config($config) {
    if ($config.source -notin @('static','dhcp') -or $config.register -notin @('both','primary','none')) {
        throw 'Invalid backup mode.'
    }
    $addresses = @($config.servers | Where-Object { $_ })
    foreach ($s in $addresses) {
        if ([ipaddress]::Parse($s).AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
            throw 'Invalid IPv4 address in backup.'
        }
    }
    $args4 = @('interface','ipv4','set','dnsservers',"name=$InterfaceIndex","source=$($config.source)","register=$($config.register)")
    if ($config.source -eq 'static') {
        $first = if ($addresses.Count) { $addresses[0] } else { 'none' }
        $args4 += "address=$first"
    }
    Invoke-LabNetsh -Arguments ($args4 + 'validate=no') | Out-Host
    if ($config.source -eq 'static') {
        for ($i = 1; $i -lt $addresses.Count; $i++) {
            Invoke-LabNetsh -Arguments @('interface','ipv4','add','dnsservers',"name=$InterfaceIndex",
                                         "address=$($addresses[$i])","index=$($i+1)",'validate=no') | Out-Host
        }
    }
    $actual = Read-IPv4Config
    if ($actual.source -ne $config.source -or $actual.register -ne $config.register -or
        ($config.source -eq 'static' -and ($actual.servers -join ',') -ne ($addresses -join ','))) {
        throw 'Windows did not report the expected IPv4 DNS settings. Keep the backup for recovery.'
    }
}

if ($Action -eq 'Preview') {
    $cfg = Read-IPv4Config
    [pscustomobject]@{Adapter=$adapter.Name; Index=$InterfaceIndex; IPv4Source=$cfg.source; IPv4Servers=($cfg.servers -join ',');
        IPv6Servers=((Get-LabServers -Index $InterfaceIndex -Family IPv6) -join ',');
        RecoveryFile=$backupPath}
    return
}
if (!(Test-LabAdministrator)) { throw 'Open PowerShell as Administrator inside the lab VM.' }
$phrase = "$Action $InterfaceIndex"
if ((Read-Host "Lab VM only. Type '$phrase' for adapter '$($adapter.Name)'") -cne $phrase) { throw 'Cancelled.' }
if ($Action -eq 'Restore') {
    $saved = Get-Content -LiteralPath $backupPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($saved.guid -ne $guid -or $saved.computer -ne $env:COMPUTERNAME) { throw 'Backup belongs to another adapter or computer.' }
    Set-IPv4Config $saved.ipv4
    # The JSON records the adapter GUID. A single unique ID keeps archive paths
    # short enough for tools that still use Windows' legacy path length limit.
    $archive = "$([guid]::NewGuid()).restored.json"
    Rename-Item -LiteralPath $backupPath -NewName $archive
    Write-Host 'Original IPv4 DNS source and registration restored. IPv6 settings were preserved.'
    return
}
$cfg = Read-IPv4Config
if (Test-Path -LiteralPath $backupPath) { throw "Restore the existing backup first: $backupPath" }
if (!(Test-LabProxy)) {
    throw 'Start and verify the lab DNS proxy on 127.0.0.1:53 UDP and TCP first.'
}
[void][IO.Directory]::CreateDirectory($backupDir)
$saved = [ordered]@{guid=$guid; computer=$env:COMPUTERNAME; ipv4=$cfg}
$stream = [IO.File]::Open($backupPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($saved | ConvertTo-Json -Depth 4))
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush($true)
} finally { $stream.Dispose() }
try {
    Set-IPv4Config ([pscustomobject]@{source='static';servers=@('127.0.0.1');register=$cfg.register})
} catch {
    $errorText = $_.Exception.Message
    try {
        Set-IPv4Config $cfg
    } catch {
        throw "Apply failed ($errorText). Automatic rollback also failed ($($_.Exception.Message)). Original backup retained: $backupPath"
    }
    throw "Apply failed. IPv4 was rolled back. Backup retained. $errorText"
}
Write-Host "Only IPv4 DNS changed. To recover, use -InterfaceIndex $InterfaceIndex -Action Restore."

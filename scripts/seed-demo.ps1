param([guid]$HistoryForDevice)
. "$PSScriptRoot\common.ps1"
Set-DevEnvironment
if ($PSBoundParameters.ContainsKey('HistoryForDevice')) {
    Invoke-DevPython -m server.cli seed-history --device-id $HistoryForDevice
} else {
    Invoke-DevPython -m server.cli seed-demo
}

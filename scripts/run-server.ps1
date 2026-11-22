param([switch]$Tls, [ValidateRange(1,65535)][int]$Port = 8000)
. "$PSScriptRoot\common.ps1"
Set-DevEnvironment
$options = @('--host','127.0.0.1','--port',"$Port",'--no-access-log','--no-proxy-headers')
if ($Tls) {
    $env:OGK_DEV_HTTP = '0'
    Invoke-DevPython -m scripts.lab_certificate
    $options += @('--ssl-certfile','.local/tls/server.pem','--ssl-keyfile','.local/tls/server-key.pem')
} else { $env:OGK_DEV_HTTP = '1' }
Invoke-DevPython -m server.cli migrate
Invoke-DevPython -m uvicorn server.main:create_app --factory @options

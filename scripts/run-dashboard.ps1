param([switch]$Tls, [ValidateRange(1,65535)][int]$Port = 8000)
# The dashboard and the API use the same listener and origin.
& "$PSScriptRoot\run-server.ps1" -Port $Port -Tls:$Tls

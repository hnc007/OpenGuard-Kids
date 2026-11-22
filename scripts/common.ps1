$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DevPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $RepoRoot

function Invoke-DevPython {
    if (-not (Test-Path -LiteralPath $DevPython)) { throw 'Run scripts\setup-dev.ps1 first.' }
    & $DevPython @args
    if ($LASTEXITCODE -ne 0) { throw "Python exited with code $LASTEXITCODE." }
}
function Set-DevEnvironment {
    $directory = Join-Path $RepoRoot '.local'
    [void][IO.Directory]::CreateDirectory($directory)
    if (-not $env:OGK_SECRET_KEY) {
        $keyFile = Join-Path $directory 'dev-key.dpapi'
        if (-not (Test-Path -LiteralPath $keyFile)) {
            $keyBytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($keyBytes) } finally { $rng.Dispose() }
            $secure = ConvertTo-SecureString ([Convert]::ToBase64String($keyBytes)) -AsPlainText -Force
            $encrypted = ConvertFrom-SecureString $secure
            $writer = New-Object IO.FileStream($keyFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $data = [Text.Encoding]::ASCII.GetBytes($encrypted)
                $writer.Write($data, 0, $data.Length)
            } finally { $writer.Dispose() }
        }
        $secure = ConvertTo-SecureString ([IO.File]::ReadAllText($keyFile))
        $credential = New-Object Management.Automation.PSCredential('ogk-dev', $secure)
        $env:OGK_SECRET_KEY = $credential.GetNetworkCredential().Password
    }
    if (-not $env:OGK_DATABASE_URL) {
        $file = (Join-Path $directory 'dev.sqlite3').Replace('\','/')
        $env:OGK_DATABASE_URL = "sqlite:///$file"
    }
    $env:PYTHONUTF8 = '1'
}

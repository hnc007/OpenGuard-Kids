#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Preview','Install','Start','Remove','Rollback')][string]$Action = 'Preview',
    [string]$ChildSid, [int]$ChildId, [string]$DeviceId, [string]$Server, [string]$Pin, [string]$Ca
)
$ErrorActionPreference = 'Stop'
$programs = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
$root = Join-Path $programs 'OpenGuardKidsLab'
$runtime = Join-Path $root '.venv\Scripts\python.exe'
$helper = Join-Path $root 'scripts\lab\service_admin.py'
if ($Action -eq 'Preview') {
    [pscustomobject]@{ Service='OpenGuardKidsLab'; Account='NT AUTHORITY\LocalService'; StartType='Manual';
        Image="$runtime -I -B ...\agent\lab_service.py --scm"; ProgramRoot=$root;
        Data=(Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'OpenGuardKidsLab');
        Recovery='Restart after 60s, 120s, then stop. Reset after 24 hours.' }
    return
}
if ([Environment]::Is64BitProcess -ne $true -or
    [IO.Path]::GetFullPath($PSScriptRoot) -ne (Join-Path $root 'scripts\lab')) {
    throw 'Use 64-bit Windows PowerShell and the protected copy in Program Files\OpenGuardKidsLab on the lab VM.'
}

function Assert-TrustedItem($item) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point: $($item.FullName)" }
    $acl = Get-Acl -LiteralPath $item.FullName
    $trusted = @('S-1-5-18', 'S-1-5-32-544',
        'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    $sidType = [Security.Principal.SecurityIdentifier]
    if ($trusted -notcontains $acl.GetOwner($sidType).Value) { throw "Untrusted owner: $($item.FullName)" }
    # WRITE_DATA, APPEND_DATA, WRITE_EA, DELETE_CHILD, WRITE_ATTRIBUTES,
    # DELETE, WRITE_DAC, WRITE_OWNER plus generic write/all.
    $write = 0x500D0156
    $rules = $acl.GetAccessRules($true, $true, $sidType)
    if (!$rules.Count) { throw "Missing file ACL: $($item.FullName)" }
    foreach ($rule in $rules) {
        $sid = $rule.IdentityReference.Value
        if ($sid -eq 'S-1-3-0' -and ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)) { continue }
        if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights.value__ -band $write) -ne 0 -and
            $trusted -notcontains $sid) { throw "Untrusted write access: $($item.FullName)" }
    }
}

function Assert-TrustedTree([string]$path) {
    $item = Get-Item -LiteralPath $path -Force
    Assert-TrustedItem $item
    if ($item.PSIsContainer) {
        foreach ($entry in Get-ChildItem -LiteralPath $path -Force) {
            Assert-TrustedTree $entry.FullName
        }
    }
}
$current = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$current.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open an Administrator shell inside the lab VM.' }
Assert-TrustedItem (Get-Item -LiteralPath $programs)
Assert-TrustedTree $root
$venv = Get-Content -LiteralPath (Join-Path $root '.venv\pyvenv.cfg') -Raw
if ($venv -notmatch '(?im)^include-system-site-packages\s*=\s*false\s*$') { throw 'Use an isolated venv.' }
if ($venv -notmatch '(?im)^home\s*=\s*(.+?)\s*$') { throw 'Cannot verify base interpreter.' }
$base = [IO.Path]::GetFullPath($matches[1].Trim())
if (!$base.StartsWith($programs + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Base Python must also be in Program Files.' }
if ($venv -match '(?im)^executable\s*=\s*(.+?)\s*$') {
    $exe = [IO.Path]::GetFullPath($matches[1].Trim())
    if ((Split-Path $exe -Parent) -ne $base) { throw 'Venv executable and base home must match.' }
}
$ancestor = Split-Path $base -Parent
while ($ancestor -ne $programs) {
    Assert-TrustedItem (Get-Item -LiteralPath $ancestor -Force)
    $ancestor = Split-Path $ancestor -Parent
}
Assert-TrustedTree $base
$options = @($Action)
if ($Action -eq 'Install') {
    if (!$ChildSid -or $ChildId -lt 1 -or !$DeviceId -or !$Server -or !$Pin) {
        throw 'Install needs -ChildSid -ChildId -DeviceId -Server https://... and -Pin.'
    }
    $options += @('--sid',$ChildSid,'--child',[string]$ChildId,'--device',$DeviceId,'--origin',$Server,'--pin',$Pin)
    if ($Ca) {
        $file = Get-Item -LiteralPath $Ca
        if (!$file.FullName.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or $file.Length -gt 131072) {
            throw 'Place the public PEM CA in the protected lab install root.'
        }
        $options += @('--ca',$file.FullName)
    }
}
$oldPath = $env:Path
Push-Location -LiteralPath $root
try {
    $env:Path = ([Environment]::SystemDirectory, $base, (Split-Path $runtime -Parent)) -join ';'
    & $runtime -I -B $helper @options
    if ($LASTEXITCODE -ne 0) { throw "Lab service action exited with code $LASTEXITCODE." }
} finally {
    $env:Path = $oldPath
    Pop-Location
}

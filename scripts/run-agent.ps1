param([ValidateSet('simulate','enroll','sync','tray')][string]$Mode='simulate',
      [string]$Server='http://127.0.0.1:8000', [string]$Store='.local/agent.sqlite3',
      [string]$Ca, [string]$Pin)
. "$PSScriptRoot\common.ps1"
$env:PYTHONUTF8='1'
if ($Mode -eq 'simulate') {
    Invoke-DevPython -m agent.simulate
} else {
    $options = @('--server',$Server,'--store',$Store)
    if ($Ca) { $options += @('--ca',$Ca) }
    if ($Pin) { $options += @('--pin',$Pin) }
    if ($Mode -eq 'tray') { Invoke-DevPython -m agent.tray @options }
    else { Invoke-DevPython -m agent.client $Mode @options }
}

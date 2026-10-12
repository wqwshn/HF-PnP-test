$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskVenv = Join-Path $taskRoot '.venv/Scripts/python.exe'
if (Test-Path $taskVenv) {
    $taskPython = $taskVenv
} else {
    $taskPython = (Get-Command python -ErrorAction Stop).Source
}
$taskUrl = 'http://127.0.0.1:8769'
try {
    $taskProbe = Invoke-RestMethod "$taskUrl/api/defaults" -TimeoutSec 2
    if ($taskProbe.channel -eq 'Ut1' -and $taskProbe.diameter -eq 19) {
        Start-Process $taskUrl
        exit
    }
} catch {}
$taskAppPath = Join-Path $taskRoot 'app.py'
$taskArguments = '"{0}" --port 8769 --open-browser' -f $taskAppPath
Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'server.log') -RedirectStandardError (Join-Path $taskRoot 'server-error.log')

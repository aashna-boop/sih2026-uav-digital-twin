param(
    [ValidateSet('replay', 'sitl', 'auto')]
    [string]$TelemetryMode = 'replay',
    [string]$MavlinkEndpoint = 'udpin:0.0.0.0:14550'
)

$ErrorActionPreference = 'Stop'
$python = 'C:\Users\rishu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:AEGISTWIN_TELEMETRY_MODE = $TelemetryMode
$env:AEGISTWIN_MAVLINK_ENDPOINT = $MavlinkEndpoint

if (-not (Test-Path -LiteralPath '.venv')) {
    & $python -m venv .venv
}

$venvPython = Join-Path $PWD '.venv\Scripts\python.exe'
& $venvPython -c 'import fastapi, uvicorn, sklearn, pandas, pymavlink, xgboost'
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r requirements.txt
}
& $venvPython -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

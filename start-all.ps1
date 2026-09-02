param(
    [switch]$WithSITL,
    [string]$WslDistro = 'Ubuntu-24.04'
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$powershell = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$logDirectory = Join-Path $projectRoot '.logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

$telemetryMode = if ($WithSITL) { 'sitl' } else { 'replay' }
$mavlinkEndpoint = 'udpin:0.0.0.0:14550'
$sitl = $null
if ($WithSITL) {
    $wslAddresses = (& wsl -d $WslDistro -- hostname -I).Trim() -split '\s+'
    $wslAddress = $wslAddresses | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1
    if (-not $wslAddress) {
        throw "Could not determine the IPv4 address of WSL distro '$WslDistro'."
    }
    $mavlinkEndpoint = "tcp:${wslAddress}:5770"
    $sitlOptions = @{
        FilePath = $powershell
        ArgumentList = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $projectRoot 'start-sitl.ps1'), '-WslDistro', $WslDistro
        )
        WorkingDirectory = $projectRoot
        WindowStyle = 'Hidden'
        RedirectStandardOutput = (Join-Path $logDirectory 'sitl.out.log')
        RedirectStandardError = (Join-Path $logDirectory 'sitl.err.log')
        PassThru = $true
    }
    $sitl = Start-Process @sitlOptions
}

$backendOptions = @{
    FilePath = $powershell
    ArgumentList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $projectRoot 'start-backend.ps1'),
        '-TelemetryMode', $telemetryMode,
        '-MavlinkEndpoint', $mavlinkEndpoint
    )
    WorkingDirectory = $projectRoot
    WindowStyle = 'Hidden'
    PassThru = $true
}
$backend = Start-Process @backendOptions

$dashboardOptions = @{
    FilePath = $powershell
    ArgumentList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $projectRoot 'start-dashboard.ps1'))
    WorkingDirectory = $projectRoot
    WindowStyle = 'Hidden'
    PassThru = $true
}
$dashboard = Start-Process @dashboardOptions

@{
    backend_pid = $backend.Id
    dashboard_pid = $dashboard.Id
    sitl_pid = if ($sitl) { $sitl.Id } else { $null }
    telemetry_mode = $telemetryMode
    mavlink_endpoint = $mavlinkEndpoint
    wsl_distro = $WslDistro
    started_at = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot '.runtime-pids.json')

Write-Host 'AegisTwin is starting...'
Write-Host 'Dashboard: http://127.0.0.1:4173'
Write-Host 'API docs:  http://127.0.0.1:8000/docs'
Write-Host "Telemetry: $telemetryMode"
Write-Host "MAVLink:   $mavlinkEndpoint"
if ($WithSITL) {
    Write-Host 'SITL log:  .logs\sitl.out.log'
}

$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.runtime-pids.json'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'No AegisTwin runtime PID file was found.'
    exit 0
}

$runtime = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
if ($runtime.sitl_pid) {
    $distro = if ($runtime.wsl_distro) { $runtime.wsl_distro } else { 'Ubuntu-24.04' }
    & (Join-Path $PSScriptRoot 'stop-sitl.ps1') -WslDistro $distro
}
foreach ($processId in @($runtime.backend_pid, $runtime.dashboard_pid, $runtime.sitl_pid)) {
    if ($processId) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $processId /T /F 2>$null | Out-Null
    }
}
Remove-Item -LiteralPath $pidFile -Force
Write-Host 'AegisTwin background processes stopped.'

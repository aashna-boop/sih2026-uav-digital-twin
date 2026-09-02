param(
    [string]$WslDistro = 'Ubuntu-24.04'
)

$ErrorActionPreference = 'Stop'
$windowsPidFile = Join-Path $PSScriptRoot '.sitl-linux.pid'
$windowsStopScript = Join-Path $PSScriptRoot 'scripts\stop-sitl.sh'
if (-not (Test-Path -LiteralPath $windowsPidFile)) {
    Write-Host 'No AegisTwin SITL PID file was found.'
    exit 0
}

function ConvertTo-WslPath([string]$Path) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Expected an absolute Windows drive path, received '$fullPath'."
    }
    $drive = $Matches[1].ToLowerInvariant()
    $remainder = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$remainder"
}

$wslPidFile = ConvertTo-WslPath $windowsPidFile
$wslStopScript = ConvertTo-WslPath $windowsStopScript
& wsl -d $WslDistro -- bash $wslStopScript $wslPidFile
Write-Host 'ArduPlane SITL stop signal sent.'

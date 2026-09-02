param(
    [string]$WslDistro = 'Ubuntu-24.04'
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$windowsScript = Join-Path $projectRoot 'scripts\start-sitl.sh'
$windowsPidFile = Join-Path $projectRoot '.sitl-linux.pid'

function ConvertTo-WslPath([string]$Path) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Expected an absolute Windows drive path, received '$fullPath'."
    }
    $drive = $Matches[1].ToLowerInvariant()
    $remainder = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$remainder"
}

$wslScript = ConvertTo-WslPath $windowsScript
$wslPidFile = ConvertTo-WslPath $windowsPidFile

& wsl -d $WslDistro -- env "AEGISTWIN_SITL_PID_FILE=$wslPidFile" bash $wslScript
exit $LASTEXITCODE

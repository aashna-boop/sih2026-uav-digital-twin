param(
    [string]$WslDistro = 'Ubuntu-24.04'
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-all.ps1') -WithSITL -WslDistro $WslDistro
exit $LASTEXITCODE


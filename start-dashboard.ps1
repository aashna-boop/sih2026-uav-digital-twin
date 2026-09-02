$ErrorActionPreference = 'Stop'
$pnpm = 'C:\Users\rishu\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd'
$nodeBin = 'C:\Users\rishu\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin'
$env:PATH = "$nodeBin;$env:PATH"

Push-Location frontend
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) {
        & $pnpm install
    }
    if (-not (Test-Path -LiteralPath 'dist\index.html')) {
        & $pnpm run build
    }
    & $pnpm run preview
}
finally {
    Pop-Location
}

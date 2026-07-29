# Freezes the Python sidecar into a single exe for bundling.
#
#   .\scripts\build-sidecar.ps1
#
# Output lands in dist-sidecar\, which tauri.conf.json bundles next to the app
# exe. Run this before "npm run build".
#
# Plain PowerShell on purpose: no backtick line-continuations (a trailing space
# after one silently breaks the command), no escape sequences.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

try {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) {
        $py = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $py) {
        throw "Python not found. Install it with: winget install Python.Python.3.12"
    }

    $venvPython = Join-Path $root ".venv\Scripts\python.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Host "Creating .venv..." -ForegroundColor Cyan
        & $py.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    }

    Write-Host "Installing sidecar and PyInstaller..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

    & $venvPython -m pip install ./sidecar pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

    Write-Host "Freezing sidecar..." -ForegroundColor Cyan

    $pyiArgs = @(
        "-m", "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--name", "reminders-sidecar",
        "--distpath", "dist-sidecar",
        "--workpath", "build\pyinstaller",
        "--specpath", "build",
        "--console",
        # keyring reaches its Windows backend dynamically, so PyInstaller's
        # import scanner cannot see it.
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "win32timezone",
        "--collect-submodules", "pyicloud",
        "sidecar\reminders_sidecar\__main__.py"
    )

    & $venvPython $pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $exe = Join-Path $root "dist-sidecar\reminders-sidecar.exe"
    if (-not (Test-Path $exe)) {
        throw "PyInstaller reported success but the exe is missing: $exe"
    }

    # A frozen exe that cannot start is worse than a build failure, because it
    # only surfaces later as a dead app. Prove it answers before bundling it.
    Write-Host "Smoke-testing the frozen exe..." -ForegroundColor Cyan
    $probeDir = Join-Path $env:TEMP "reminders-sync-buildcheck"
    $out = '{"id":1,"method":"ping","params":{}}' | & $exe --data-dir $probeDir 2>$null
    if ($out -match '"pong"') {
        Write-Host "OK: sidecar responds to ping" -ForegroundColor Green
    } else {
        Write-Host "WARNING: the frozen sidecar did not answer ping." -ForegroundColor Yellow
        Write-Host "Output was:" -ForegroundColor Yellow
        Write-Host $out
        Write-Host "It will still be bundled, but the app may not start." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host ("Built: " + $exe) -ForegroundColor Green
    Write-Host "Next: npm run build"
    Write-Host ""
}
finally {
    Pop-Location
}

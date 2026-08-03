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

    # Entry point is run_sidecar.py, NOT reminders_sidecar/__main__.py.
    # PyInstaller runs the given script as a top-level module, so freezing
    # __main__.py directly leaves its relative imports with no parent package
    # and the exe dies at startup with
    #   ImportError: attempted relative import with no known parent package
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
        "--paths", "sidecar",
        "--collect-submodules", "reminders_sidecar",
        # These reach for modules and data files the import scanner cannot see:
        # pydantic builds models at runtime, keyring picks its backend
        # dynamically, and fido2 (pulled in by pyicloud for security-key 2FA)
        # ships a public_suffix_list.dat that must travel with it.
        "--collect-all", "pyicloud",
        "--collect-all", "pydantic",
        "--collect-all", "keyring",
        "--collect-all", "fido2",
        "--collect-all", "srp",
        # tzdata is a pure data package -- zoneinfo has no tz database to fall
        # back on under Windows, so without this every named zone fails to
        # resolve and due dates land back on a fixed offset.
        "--collect-all", "tzdata",
        "--collect-all", "tzlocal",
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "win32timezone",
        "sidecar\run_sidecar.py"
    )

    & $venvPython $pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $exe = Join-Path $root "dist-sidecar\reminders-sidecar.exe"
    if (-not (Test-Path $exe)) {
        throw "PyInstaller reported success but the exe is missing: $exe"
    }

    # A frozen exe that cannot start is worse than a build failure, because it
    # only surfaces later as a dead app. Prove it answers before bundling it.
    #
    # stderr is captured, not discarded: when the exe dies it dies with a Python
    # traceback on stderr, and that traceback is the entire diagnosis.
    Write-Host "Smoke-testing the frozen exe..." -ForegroundColor Cyan
    $probeDir = Join-Path $env:TEMP "reminders-sync-buildcheck"

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $probeOut = ('{"id":1,"method":"ping","params":{}}' |
                 & $exe --data-dir $probeDir 2>&1 | Out-String)
    $ErrorActionPreference = $prevEAP

    if ($probeOut -match '"pong"') {
        Write-Host "OK: sidecar responds to ping" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "The frozen sidecar did not answer. Its output:" -ForegroundColor Red
        Write-Host "----------------------------------------------------------------"
        Write-Host $probeOut
        Write-Host "----------------------------------------------------------------"
        throw "Frozen sidecar failed its smoke test. The app would not start with this build."
    }

    Write-Host ""
    Write-Host ("Built: " + $exe) -ForegroundColor Green
    Write-Host "Next: npm run build"
    Write-Host ""
}
finally {
    Pop-Location
}

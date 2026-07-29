# Freezes the Python sidecar into a single exe for bundling.
#
# Run before `npm run build`. Output lands in dist-sidecar\, which
# tauri.conf.json bundles next to the app exe.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location $root
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating .venv..."
        py -m venv .venv
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install ./sidecar pyinstaller

    Write-Host "Freezing sidecar..."
    & .\.venv\Scripts\pyinstaller.exe `
        --onefile `
        --name reminders-sidecar `
        --distpath dist-sidecar `
        --workpath build\pyinstaller `
        --specpath build `
        --console `
        --hidden-import keyring.backends.Windows `
        --hidden-import win32timezone `
        sidecar\reminders_sidecar\__main__.py

    if (-not (Test-Path "dist-sidecar\reminders-sidecar.exe")) {
        throw "PyInstaller did not produce dist-sidecar\reminders-sidecar.exe"
    }
    Write-Host "OK: dist-sidecar\reminders-sidecar.exe" -ForegroundColor Green
}
finally {
    Pop-Location
}

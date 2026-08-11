# Builds the native Rust sidecar published beside the WinUI application.
#
#   .\scripts\build-sidecar.ps1 -Architecture x64
#   .\scripts\build-sidecar.ps1 -Architecture ARM64

[CmdletBinding()]
param(
    [ValidateSet('x64', 'ARM64')]
    [string]$Architecture = 'x64'
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

try {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw "Rust is not installed. Install the current stable toolchain from https://rustup.rs and reopen PowerShell."
    }
    if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
        throw 'rustup is required to verify the architecture-specific Rust target.'
    }

    $target = if ($Architecture -eq 'ARM64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
    $installedTargets = @(& rustup target list --installed)
    if ($installedTargets -notcontains $target) {
        throw "Rust target '$target' is not installed. Add it with: rustup target add $target"
    }

    Write-Host "Building the native Rust sync service for $Architecture..." -ForegroundColor Cyan
    & cargo build --manifest-path sidecar\Cargo.toml --release --target $target
    if ($LASTEXITCODE -ne 0) { throw "Rust sidecar build failed" }

    $source = Join-Path $root "sidecar\target\$target\release\reminders-sidecar.exe"
    $destinationDir = if ($Architecture -eq 'ARM64') { Join-Path $root 'dist-sidecar\arm64' } else { Join-Path $root 'dist-sidecar' }
    $destination = Join-Path $destinationDir "reminders-sidecar.exe"
    if (-not (Test-Path $source)) {
        throw "Cargo reported success but the sidecar executable is missing: $source"
    }

    New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    Copy-Item -Force $source $destination

    $hostArchitecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    if (($Architecture -eq 'ARM64' -and $hostArchitecture -eq 'Arm64') -or ($Architecture -eq 'x64' -and $hostArchitecture -eq 'X64')) {
        Write-Host "Smoke-testing the JSON protocol..." -ForegroundColor Cyan
        $probeDir = Join-Path $env:TEMP "reminders-sync-buildcheck-$PID"
        try {
            $probeOut = ('{"id":1,"method":"shutdown","params":{}}' | & $destination --data-dir $probeDir 2>&1 | Out-String)
            if ($probeOut -notmatch '"bye":true') {
                Write-Host $probeOut
                throw "The Rust sidecar did not answer its protocol smoke test"
            }
        }
        finally {
            if (Test-Path -LiteralPath $probeDir) { Remove-Item -LiteralPath $probeDir -Recurse -Force }
        }
    }
    else {
        Write-Host "Skipping execution smoke test for cross-compiled $Architecture binary." -ForegroundColor Yellow
    }

    Write-Host ("Built: " + $destination) -ForegroundColor Green
    Write-Host "Next: .\scripts\build-windows.ps1 -Architecture $Architecture -SkipSidecar"
}
finally {
    Pop-Location
}

# Verifies the build toolchain before you spend time on a build that can't finish.
#
#   .\scripts\check-prereqs.ps1
#
# Deliberately plain PowerShell: no backtick line-continuations, no escape
# sequences, no ${env:...} interpolation. Windows PowerShell 5.1 is the floor.

$problems = New-Object System.Collections.ArrayList

function Test-Tool {
    param(
        [string]$Name,
        [string]$Command,
        [string]$FixHint
    )

    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Host ("  [MISSING] " + $Name) -ForegroundColor Red
        Write-Host ("            " + $FixHint) -ForegroundColor DarkGray
        [void]$problems.Add($Name)
        return
    }

    $version = "(installed)"
    try {
        $raw = & $Command --version 2>&1 | Select-Object -First 1
        if ($raw) { $version = $raw.ToString().Trim() }
    } catch {
        # Some tools exit non-zero for --version; presence is what matters.
    }
    Write-Host ("  [ok]      " + $Name + " - " + $version) -ForegroundColor Green
}

Write-Host ""
Write-Host "Build prerequisites" -ForegroundColor Cyan
Write-Host ""

Test-Tool -Name "Rust (cargo)" -Command "cargo" -FixHint "winget install Rustlang.Rustup   (then reopen PowerShell)"
Test-Tool -Name "Node" -Command "node" -FixHint "winget install OpenJS.NodeJS.LTS"
Test-Tool -Name "npm" -Command "npm" -FixHint "ships with Node"
Test-Tool -Name "Python" -Command "py" -FixHint "winget install Python.Python.3.12"

Write-Host ""
Write-Host "Native toolchain" -ForegroundColor Cyan
Write-Host ""

if (Get-Command rustup -ErrorAction SilentlyContinue) {
    $hostLine = rustup show 2>&1 | Select-String "Default host:"
    if ($hostLine) {
        $hostText = $hostLine.ToString()
        if ($hostText -match "msvc") {
            Write-Host "  [ok]      rustup host is MSVC" -ForegroundColor Green
        } else {
            Write-Host ("  [WARN]    " + $hostText.Trim()) -ForegroundColor Yellow
            Write-Host "            rustup default stable-x86_64-pc-windows-msvc" -ForegroundColor DarkGray
            [void]$problems.Add("MSVC toolchain")
        }
    }
}

# The MSVC linker is separate from rustup and is the usual second surprise:
# it surfaces late, as "link.exe not found" partway through compiling.
$roots = @()
$pf = [Environment]::GetEnvironmentVariable("ProgramFiles")
$pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
if ($pf) { $roots += $pf }
if ($pf86) { $roots += $pf86 }

$linker = $null
foreach ($root in $roots) {
    $pattern = Join-Path $root "Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Host*\x64\link.exe"
    $hit = Get-ChildItem $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { $linker = $hit; break }
}

if ($linker) {
    Write-Host "  [ok]      MSVC linker found" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] MSVC linker (link.exe)" -ForegroundColor Red
    Write-Host "            winget install Microsoft.VisualStudio.2022.BuildTools" -ForegroundColor DarkGray
    Write-Host "            then tick 'Desktop development with C++' in the installer" -ForegroundColor DarkGray
    [void]$problems.Add("MSVC linker")
}

# WebView2 is preinstalled on Windows 11, not always on 10.
Write-Host ""
Write-Host "Runtime" -ForegroundColor Cyan
Write-Host ""

$wv2Keys = @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
$wv2 = $null
foreach ($key in $wv2Keys) {
    if (Test-Path $key) { $wv2 = $key; break }
}

if ($wv2) {
    $props = Get-ItemProperty $wv2 -ErrorAction SilentlyContinue
    $ver = "installed"
    if ($props -and $props.pv) { $ver = $props.pv }
    Write-Host ("  [ok]      WebView2 runtime - " + $ver) -ForegroundColor Green
} else {
    Write-Host "  [WARN]    WebView2 runtime not detected" -ForegroundColor Yellow
    Write-Host "            https://developer.microsoft.com/microsoft-edge/webview2/" -ForegroundColor DarkGray
    Write-Host "            Needed to run the app, not to build it." -ForegroundColor DarkGray
}

Write-Host ""
if ($problems.Count -eq 0) {
    Write-Host "All good. Next:" -ForegroundColor Green
    Write-Host "  npm install"
    Write-Host "  .\scripts\build-sidecar.ps1"
    Write-Host "  npm run build"
    Write-Host ""
    exit 0
}

Write-Host ("Missing: " + ($problems -join ", ")) -ForegroundColor Red
Write-Host "Install those, reopen PowerShell, and run this again."
Write-Host ""
exit 1

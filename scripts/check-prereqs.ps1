# Verifies the build toolchain before you spend time on a build that can't finish.
#
#   .\scripts\check-prereqs.ps1

$ErrorActionPreference = "Continue"
$problems = @()

function Test-Tool {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$VersionArgs = @("--version"),
        [string]$FixHint
    )
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Host "  [MISSING] $Name" -ForegroundColor Red
        Write-Host "            $FixHint" -ForegroundColor DarkGray
        $script:problems += $Name
        return
    }
    try {
        $version = (& $Command @VersionArgs 2>&1 | Select-Object -First 1)
    } catch {
        $version = "(installed)"
    }
    Write-Host "  [ok]      $Name — $version" -ForegroundColor Green
}

Write-Host "`nBuild prerequisites`n" -ForegroundColor Cyan

Test-Tool -Name "Rust (cargo)" -Command "cargo" `
    -FixHint "winget install Rustlang.Rustup  — then reopen PowerShell"

Test-Tool -Name "Node" -Command "node" `
    -FixHint "winget install OpenJS.NodeJS.LTS"

Test-Tool -Name "Python" -Command "py" -VersionArgs @("--version") `
    -FixHint "winget install Python.Python.3.12"

# The MSVC linker is separate from rustup and is the usual second surprise.
Write-Host "`nNative toolchain`n" -ForegroundColor Cyan
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    $host_line = (rustup show 2>&1 | Select-String "Default host:")
    if ($host_line -and $host_line -notmatch "msvc") {
        Write-Host "  [WARN]    Default host is not MSVC: $host_line" -ForegroundColor Yellow
        Write-Host "            rustup default stable-x86_64-pc-windows-msvc" -ForegroundColor DarkGray
        $problems += "MSVC toolchain"
    } else {
        Write-Host "  [ok]      rustup host is MSVC" -ForegroundColor Green
    }
}

$linker = Get-ChildItem `
    "${env:ProgramFiles}\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Host*\x64\link.exe", `
    "${env:ProgramFiles(x86)}\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Host*\x64\link.exe" `
    -ErrorAction SilentlyContinue | Select-Object -First 1
if ($linker) {
    Write-Host "  [ok]      MSVC linker found" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] MSVC linker (link.exe)" -ForegroundColor Red
    Write-Host "            winget install Microsoft.VisualStudio.2022.BuildTools" -ForegroundColor DarkGray
    Write-Host "            then tick 'Desktop development with C++' in the installer" -ForegroundColor DarkGray
    $problems += "MSVC linker"
}

# WebView2 is preinstalled on Windows 11 but not always on 10.
Write-Host "`nRuntime`n" -ForegroundColor Cyan
$wv2Keys = @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
$wv2 = $wv2Keys | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($wv2) {
    $v = (Get-ItemProperty $wv2 -ErrorAction SilentlyContinue).pv
    Write-Host "  [ok]      WebView2 runtime — $v" -ForegroundColor Green
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
    Write-Host "  npm run build`n"
    exit 0
}
Write-Host ("Missing: " + ($problems -join ", ")) -ForegroundColor Red
Write-Host "Install those, reopen PowerShell, and run this again.`n"
exit 1

[CmdletBinding()]
param(
    [switch]$Demo,
    [ValidateSet('Auto', 'x64', 'ARM64')]
    [string]$Architecture = 'Auto'
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$resolvedArchitecture = if ($Architecture -ne 'Auto') { $Architecture } elseif ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq [Runtime.InteropServices.Architecture]::Arm64) { 'ARM64' } else { 'x64' }
$runtime = if ($resolvedArchitecture -eq 'ARM64') { 'win-arm64' } else { 'win-x64' }
$sidecar = if ($resolvedArchitecture -eq 'ARM64') { Join-Path $repository 'dist-sidecar\arm64\reminders-sidecar.exe' } else { Join-Path $repository 'dist-sidecar\reminders-sidecar.exe' }
$project = Join-Path $repository 'src-windows\Reminders.WinUI\Reminders.WinUI.csproj'

if (-not $Demo -and -not (Test-Path -LiteralPath $sidecar)) {
    & (Join-Path $PSScriptRoot 'build-sidecar.ps1') -Architecture $resolvedArchitecture
    if ($LASTEXITCODE -ne 0) { throw 'The Rust sidecar build failed.' }
}

$env:REMINDERS_SIDECAR = if (Test-Path -LiteralPath $sidecar) { $sidecar } else { $null }
if ($Demo) { dotnet run --project $project --configuration Debug --runtime $runtime -p:Platform=$resolvedArchitecture -- --demo }
else { dotnet run --project $project --configuration Debug --runtime $runtime -p:Platform=$resolvedArchitecture }

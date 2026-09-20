[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [ValidateSet('x64', 'ARM64')]
    [string]$Architecture = 'x64',
    [switch]$SkipSidecar
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$sidecar = if ($Architecture -eq 'ARM64') { Join-Path $repository 'dist-sidecar\arm64\reminders-sidecar.exe' } else { Join-Path $repository 'dist-sidecar\reminders-sidecar.exe' }
$project = Join-Path $repository 'src-windows\Reminders.WinUI\Reminders.WinUI.csproj'
$runtime = if ($Architecture -eq 'ARM64') { 'win-arm64' } else { 'win-x64' }
$output = if ($Architecture -eq 'ARM64') { Join-Path $repository 'dist-windows-arm64' } else { Join-Path $repository 'dist-windows' }

if (-not $SkipSidecar -or -not (Test-Path -LiteralPath $sidecar)) {
    & (Join-Path $PSScriptRoot 'build-sidecar.ps1') -Architecture $Architecture
    if ($LASTEXITCODE -ne 0) { throw 'The Rust sidecar build failed.' }
}

dotnet restore $project --runtime $runtime --ignore-failed-sources
if ($LASTEXITCODE -ne 0) { throw 'The .NET build metadata restore failed.' }

$repositoryPath = [IO.Path]::GetFullPath($repository).TrimEnd('\')
$outputPath = [IO.Path]::GetFullPath($output).TrimEnd('\')
if (-not $outputPath.StartsWith($repositoryPath + '\', [StringComparison]::OrdinalIgnoreCase) -or $outputPath -eq $repositoryPath) {
    throw "Refusing to clean unsafe publish path: $outputPath"
}
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Recurse -Force
}

dotnet publish $project --configuration $Configuration --runtime $runtime --no-restore --self-contained true --output $output -p:Platform=$Architecture
if ($LASTEXITCODE -ne 0) { throw 'The native Windows frontend build failed.' }

$publishedSidecar = Join-Path $output 'reminders-sidecar.exe'
Copy-Item -LiteralPath $sidecar -Destination $publishedSidecar -Force
$builtSidecar = Join-Path $output 'reminders-sidecar.exe'
if (-not (Test-Path -LiteralPath $builtSidecar)) {
    throw 'The published app does not contain reminders-sidecar.exe.'
}

Write-Host "Native Windows $Architecture app: $output\Reminders.exe"

[CmdletBinding()]
param(
    [ValidateSet('x64', 'ARM64')]
    [string]$Architecture = 'x64',
    [ValidatePattern('^[A-Za-z0-9.-]{3,50}$')]
    [string]$IdentityName = 'RemindersForWindows',
    [string]$Publisher = 'CN=paulsavvas.com',
    [string]$CertificateThumbprint,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$portableOutput = if ($Architecture -eq 'ARM64') { Join-Path $repository 'dist-windows-arm64' } else { Join-Path $repository 'dist-windows' }
$packageOutput = Join-Path $repository 'dist-msix'
$project = Join-Path $repository 'src-windows\Reminders.WinUI\Reminders.WinUI.csproj'
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
$makeAppx = Get-ChildItem $sdkRoot -Recurse -Filter makeappx.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\makeappx\.exe$' } |
    Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
if (-not $makeAppx) { throw 'MakeAppx.exe was not found in the installed Windows SDK.' }

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build-windows.ps1') -Architecture $Architecture
    if ($LASTEXITCODE -ne 0) { throw "The $Architecture production build failed." }
}
if (-not (Test-Path (Join-Path $portableOutput 'Reminders.exe'))) {
    throw "The $Architecture portable build is missing. Run .\scripts\build-windows.ps1 -Architecture $Architecture first."
}

if ($CertificateThumbprint) {
    $certificate = Get-Item "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction Stop
    $Publisher = $certificate.Subject
}

[xml]$projectXml = Get-Content -Raw $project
$sourceVersion = [version]$projectXml.Project.PropertyGroup.Version
$packageVersion = "$($sourceVersion.Major).$($sourceVersion.Minor).$($sourceVersion.Build).0"
$processorArchitecture = if ($Architecture -eq 'ARM64') { 'arm64' } else { 'x64' }
$escapedPublisher = [Security.SecurityElement]::Escape($Publisher)
$staging = Join-Path ([IO.Path]::GetTempPath()) "reminders-msix-$([guid]::NewGuid().ToString('N'))"

function New-PackageLogo([string]$Source, [string]$Destination, [int]$Width, [int]$Height) {
    Add-Type -AssemblyName System.Drawing
    $sourceImage = [Drawing.Image]::FromFile($Source)
    try {
        $bitmap = [Drawing.Bitmap]::new($Width, $Height)
        try {
            $graphics = [Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.Clear([Drawing.Color]::Transparent)
                $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.DrawImage($sourceImage, 0, 0, $Width, $Height)
            }
            finally { $graphics.Dispose() }
            $bitmap.Save($Destination, [Drawing.Imaging.ImageFormat]::Png)
        }
        finally { $bitmap.Dispose() }
    }
    finally { $sourceImage.Dispose() }
}

try {
    New-Item -ItemType Directory -Path $staging, (Join-Path $staging 'Assets'), $packageOutput -Force | Out-Null
    Copy-Item -Path (Join-Path $portableOutput '*') -Destination $staging -Recurse -Force
    Get-ChildItem $staging -Filter '*.pdb' -File -ErrorAction SilentlyContinue | Remove-Item -Force
    $icon = Join-Path $repository 'src-windows\Reminders.WinUI\Assets\icon-v2.png'
    New-PackageLogo $icon (Join-Path $staging 'Assets\StoreLogo.png') 50 50
    New-PackageLogo $icon (Join-Path $staging 'Assets\Square44x44Logo.png') 44 44
    New-PackageLogo $icon (Join-Path $staging 'Assets\Square150x150Logo.png') 150 150

    $manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name="$IdentityName" Publisher="$escapedPublisher" Version="$packageVersion" ProcessorArchitecture="$processorArchitecture" />
  <Properties>
    <DisplayName>Reminders for Windows</DisplayName>
    <PublisherDisplayName>paulsavvas.com</PublisherDisplayName>
    <Description>A native Windows client for iCloud Reminders.</Description>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>
  <Resources><Resource Language="en-us" /></Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="Reminders" Executable="Reminders.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Reminders" Description="A native Windows client for iCloud Reminders."
                          BackgroundColor="transparent" Square150x150Logo="Assets\Square150x150Logo.png"
                          Square44x44Logo="Assets\Square44x44Logo.png" />
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <Capability Name="internetClient" />
  </Capabilities>
</Package>
"@
    [IO.File]::WriteAllText((Join-Path $staging 'AppxManifest.xml'), $manifest, [Text.UTF8Encoding]::new($false))
    $package = Join-Path $packageOutput "Reminders-for-Windows-$Architecture.msix"
    $packOutput = @(& $makeAppx.FullName pack /o /h SHA256 /d $staging /p $package 2>&1)
    if ($LASTEXITCODE -ne 0) { $packOutput | Write-Host; throw 'MakeAppx failed to create the MSIX package.' }
    $packOutput | Select-Object -Last 1 | Write-Host

    if ($CertificateThumbprint) {
        $signTool = Get-ChildItem $sdkRoot -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
        if (-not $signTool) { throw 'SignTool.exe was not found in the installed Windows SDK.' }
        & $signTool.FullName sign /sha1 $CertificateThumbprint /fd SHA256 $package
        if ($LASTEXITCODE -ne 0) { throw 'SignTool failed to sign the MSIX package.' }
    }
    else {
        Write-Warning 'The MSIX is unsigned. Sign it with -CertificateThumbprint or submit it to the Microsoft Store before installation.'
    }
    Write-Host "MSIX package: $package" -ForegroundColor Green
}
finally {
    $resolvedStaging = [IO.Path]::GetFullPath($staging)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedStaging.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedStaging)) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}

# Getting started on Windows

Run all commands from the repository root in PowerShell.

## Tooling

Node.js, npm, a browser runtime, and JavaScript tooling are **not required**.
The desktop interface is C# and WinUI 3. The application has two useful
development paths:

- **Demo UI:** .NET 8 SDK only. It does not build or start Rust, access the
  cache, request credentials, or connect to iCloud.
- **Complete application:** .NET 8 SDK, Rust's MSVC toolchain, and Visual
  Studio 2022 or Build Tools with the Windows SDK and matching x64 or ARM64
  MSVC tools.

NuGet may need network access on the first build to restore the Windows App SDK
packages already declared by the project. No global packages need to be
installed by this repository.

## Launch the demo UI

This is the quickest way to open the application without an account:

```powershell
.\scripts\run-windows.ps1 -Demo
```

Optionally check the demo prerequisites first:

```powershell
.\scripts\check-prereqs.ps1 -Demo
```

After a production build, the same isolated mode can be launched directly:

```powershell
.\dist-windows\Reminders.exe --demo
```

Demo reminders exist only in memory and disappear when the window closes.

## Run the complete application for development

```powershell
.\scripts\check-prereqs.ps1
.\scripts\run-windows.ps1
```

The run script builds `dist-sidecar\reminders-sidecar.exe` when it is missing,
then launches the Debug WinUI application. The normal application starts the
sidecar and displays the iCloud sign-in flow when no restored session exists.

## Create and launch a production build

```powershell
.\scripts\check-prereqs.ps1
.\scripts\build-windows.ps1
.\dist-windows\Reminders.exe
```

The production output is a self-contained x64 folder at `dist-windows`. Keep
the folder together: it contains the native executable, compiled WinUI
resources, Windows App SDK runtime files, icon, and Rust sidecar.

For a native ARM64 build:

```powershell
.\scripts\check-prereqs.ps1 -Architecture ARM64
.\scripts\build-windows.ps1 -Architecture ARM64
```

ARM64 output is written to `dist-windows-arm64`. The ARM64 Rust target
(`aarch64-pc-windows-msvc`) and Visual Studio ARM64 MSVC tools must already be
installed. The scripts report either missing prerequisite without installing
anything.

## Create an MSIX package

The package includes both the WinUI executable and architecture-matched Rust
sidecar:

```powershell
.\scripts\build-msix.ps1 -Architecture x64
.\scripts\build-msix.ps1 -Architecture ARM64
```

Packages are written to `dist-msix`. They are unsigned by default, which is
appropriate for CI artifacts and Microsoft Store submission. To produce a
sideloadable package, the manifest publisher must match a trusted code-signing
certificate in the current user's certificate store:

```powershell
.\scripts\build-msix.ps1 -Architecture x64 -CertificateThumbprint YOUR_THUMBPRINT
```

Use `-IdentityName` and `-Publisher` when preparing a Store-reserved
identity. When `-CertificateThumbprint` is supplied, the certificate subject
is used as the publisher automatically. Without either override, the manifest
uses `CN=paulsavvas.com` as its publisher identity and `paulsavvas.com` as its
displayed publisher. A signing certificate's subject must exactly match the
manifest identity; the displayed domain is not itself proof of code signing.

The packaging flow follows Microsoft's
[manual MSIX component guidance](https://learn.microsoft.com/windows/msix/desktop/desktop-to-uwp-manual-conversion)
because the package contains both the WinUI executable and Rust sidecar. See
Microsoft's [package and deployment overview](https://learn.microsoft.com/windows/apps/package-and-deploy/)
for Store and enterprise distribution choices.

To rebuild only the Rust executable:

```powershell
.\scripts\build-sidecar.ps1
```

To republish the frontend using an already-built sidecar:

```powershell
.\scripts\build-windows.ps1 -Architecture x64 -SkipSidecar
```

## Tests

```powershell
cargo test --manifest-path .\sidecar\Cargo.toml --locked
dotnet build .\src-windows\Reminders.WinUI\Reminders.WinUI.csproj -c Release -p:Platform=x64
```

The Rust tests do not require an iCloud account. Demo mode is the safe path for
interactive frontend testing.

## Troubleshooting

- If PowerShell blocks local scripts, use:
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-windows.ps1 -Demo`
- Application diagnostics are written to
  `%LOCALAPPDATA%\RemindersSync\logs\app.log`.
- Normal application data is under `%LOCALAPPDATA%\RemindersSync`; demo mode
  does not read or modify it.
- Sidebar state is a UI-only preference stored at
  `%LOCALAPPDATA%\RemindersForWindows\ui-settings.json`. Demo mode may update
  this preference, but never reminder data or credentials.
- If the sidecar is missing, run `.\scripts\build-sidecar.ps1`.

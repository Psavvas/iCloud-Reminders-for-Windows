# Native Windows frontend

The desktop client is a C# WinUI 3 application using the Windows App SDK. Its
shell is composed from Windows controls including `NavigationView`, `InfoBar`,
`CommandBar`, `ListView`, `ContentDialog`, `CalendarDatePicker`, `TimePicker`,
`ToggleSwitch`, `InfoBadge`, and the platform theme resources.

No web frontend is built or shipped. The interface is implemented entirely with
Windows UI controls and verified through Windows UI Automation.

## Process boundary

The existing Rust binary remains the data and sync backend. It receives
newline-delimited JSON requests over standard input and emits responses and
events over standard output. The native client:

- locates and starts `reminders-sidecar.exe` without a console window;
- proves readiness with `ping` before showing cached data;
- correlates concurrent calls by numeric request ID;
- forwards sync, authentication and conflict events onto the WinUI dispatcher;
- stores its data under `%LOCALAPPDATA%\RemindersSync`;
- shuts the child down when the application exits.

## Demo mode

`--demo` is a first-class, side-effect-free UI testing mode. It does not start
the sidecar or access the real cache. Sample lists and reminders live only in
memory. The sign-in screen exposes the same path through an **Explore the UI
with sample data** button.

## Build

Node.js and npm are not used. Run these commands from the repository root:

```powershell
.\scripts\run-windows.ps1
.\scripts\run-windows.ps1 -Demo
.\scripts\build-windows.ps1
.\scripts\build-windows.ps1 -Architecture ARM64
.\scripts\build-msix.ps1 -Architecture x64
```

The release output is a self-contained x64 folder under `dist-windows`.
ARM64 output is written to `dist-windows-arm64`, and MSIX packages are written
to `dist-msix`.
For prerequisites, direct executable commands, tests, and troubleshooting, see
[Getting started on Windows](getting-started.md).

# Sidecar build output

`scripts/build-sidecar.ps1` writes the native Rust `reminders-sidecar.exe` here,
and the WinUI publish step copies it next to `Reminders.exe`.

The directory is committed with this file so the project content item resolves
on a fresh checkout, before the sidecar has been built.

Build it from the repository root with:

```powershell
.\scripts\build-sidecar.ps1
```

Node.js and npm are not involved. For complete application and demo commands,
see [the Windows getting-started guide](../docs/getting-started.md).

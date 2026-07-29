# Sidecar build output

`scripts/build-sidecar.ps1` writes `reminders-sidecar.exe` here with PyInstaller,
and `tauri build` bundles everything in this directory next to the app exe.

The directory is committed with this file so the bundler's resource glob matches
on a fresh checkout, before the sidecar has been built.

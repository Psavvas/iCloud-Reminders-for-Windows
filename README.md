# iCloud Reminders for Windows

A native Windows desktop client for iCloud Reminders, with local due-date
notifications.

## What it is

- **Tauri v2 shell** — compiles to a real Win32 `.exe` with an MSI/NSIS
  installer. Uses the system WebView2 runtime rather than bundling a browser,
  so the binary is ~10 MB. Native tray, native toasts, native window chrome.
  The UI layer itself is HTML/CSS in WebView2, not WinUI controls.
- **Python sidecar** — owns iCloud access and the SQLite cache, frozen with
  PyInstaller and bundled beside the exe. Speaks newline-delimited JSON over
  stdio.
- **SQLite cache** — every read the UI performs is a local query. The network
  is never on the critical path of a click.

## Building

Requires Python 3.11+, Node 18+, and the Rust MSVC toolchain
(`rustup default stable-x86_64-pc-windows-msvc`), plus the
[WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
(preinstalled on Windows 11).

```powershell
npm install
.\scripts\build-sidecar.ps1     # freezes the Python sidecar
npm run build                   # produces the installer
```

The installer lands in `src-tauri\target\release\bundle\`.

For development:

```powershell
.\scripts\build-sidecar.ps1
$env:REMINDERS_SIDECAR = "$PWD\dist-sidecar\reminders-sidecar.exe"
npm run dev
```

> **Toasts will not appear under `npm run dev`.** Windows only delivers
> notifications for an application with a registered AppUserModelID, which is
> created by the installer. Test notifications from an installed build — this
> is a Windows constraint, not a bug in the app.

## Using it

First launch asks for your Apple ID and password, then a 2FA code. The password
goes to Windows Credential Manager via `keyring`; the session persists, so
subsequent launches skip both.

Closing the window hides it to the tray — the scheduler has to keep running for
due-date toasts to be worth anything. Quit from the tray menu.

## What it does and doesn't do

| | |
|---|---|
| Lists, reminders, detail pane | Read/write |
| Title, notes, due date, priority | Read/write |
| Tags | **Read and filter only** |
| Creating, renaming, deleting lists | **Not supported** |
| Background sync | Delta cursor, every 10 minutes |
| Due-date notifications | 30s tick, Windows toast |

The two gaps are Apple's, not oversights. Phase 1 established both against a
live account:

- **Tags can't be written.** The API accepts a hashtag write and reads it back
  correctly, but the Reminders app never renders it. Matching the exact record
  shape the iPhone writes — `Name` as `STRING` rather than pyicloud's
  `ENCRYPTED_BYTES` — did not change that. Reading works, so tags are shown and
  filterable.
- **Lists can't be created.** A raw CloudKit `List` create is accepted and comes
  back from `lists()`, but never reaches the device.

See `spike/README.md` for the full findings.

## Design notes

**Times.** Everything is stored UTC and converted at display. The cache rejects
naive datetimes outright. A date picker hands over local wall-clock time, which
the sidecar resolves against the machine's zone — Apple's API would otherwise
silently read it as UTC.

**Sleeping through due times.** Reminders overdue by more than an hour are
treated as missed while the machine was away and collapse into a single summary
toast. Freshly-due ones toast individually, up to three; beyond that they also
collapse. Waking to fourteen separate toasts is worse than useless.

**Both devices write.** Local edits go to an outbox carrying the CloudKit change
tag they were made against. If the record moved since, the push is recorded as a
conflict rather than forced: the iCloud copy wins in the cache so the UI matches
reality, and your version is preserved and offered back. This matters because
the phone was observed *replacing* a reminder's tag list wholesale rather than
merging.

**Lists on every sync.** `iter_changes()` only ever reports reminders, so a
renamed or deleted list would never appear through the delta cursor. Lists are
re-read in full on every sync pass, delta included.

## Layout

```
sidecar/        Python: iCloud client, SQLite cache, sync, notification policy
src-tauri/      Rust: window, tray, timers, sidecar IPC
src/            Frontend: sidebar, list, detail
scripts/        Icon generation, sidecar freeze
spike/          Phase 1 validation scripts and findings
```

## Tests

```powershell
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install ./sidecar pytest
cd sidecar; python -m pytest
```

Covers timezone handling, cache filtering and ordering, sync and delta
behaviour, conflict recording, the stdio protocol contract, and the sleep/wake
notification batching.

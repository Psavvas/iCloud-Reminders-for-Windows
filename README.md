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

Requires Python 3.11+, Node 18+, and the Rust MSVC toolchain, plus the
[WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
(preinstalled on Windows 11).

```powershell
winget install Rustlang.Rustup
# reopen PowerShell so PATH picks up cargo
rustup default stable-x86_64-pc-windows-msvc
```

Rust's MSVC toolchain also needs the C++ linker, which rustup does not install
itself. If a build fails with `link.exe not found`, install
`Microsoft.VisualStudio.2022.BuildTools` and tick **Desktop development with
C++**.

Check everything at once before building:

```powershell
.\scripts\check-prereqs.ps1
```

Then:

```powershell
npm install
.\scripts\build-sidecar.ps1     # freezes the Python sidecar
npm run build                   # produces the installer
```

The installer lands in `src-tauri\target\release\bundle\`.

### Seeing a change

`git pull` on its own changes nothing you can run — the frontend is bundled
into the exe, the Rust is compiled, and the sidecar is frozen. What you have to
rebuild depends on what changed:

| Changed | Installed build | `npm run dev` |
|---|---|---|
| `src/` (HTML, CSS, JS) | `npm run build`, reinstall | reload the window (Ctrl+R) |
| `src-tauri/` (Rust) | `npm run build`, reinstall | recompiles on save |
| `sidecar/` (Python) | `.\scripts\build-sidecar.ps1`, then `npm run build` | see below |
| `src-tauri/icons/` | `python scripts/make_icons.py`, rebuild, reinstall | — |

So a pull that touches everything means the full three steps again.

### Development

```powershell
.\scripts\build-sidecar.ps1
$env:REMINDERS_SIDECAR = "$PWD\dist-sidecar\reminders-sidecar.exe"
npm run dev
```

To iterate on the sidecar without re-freezing it every time, run it from source
instead:

```powershell
$env:REMINDERS_SIDECAR = "$PWD\.venv\Scripts\python.exe"
$env:REMINDERS_SIDECAR_ARGS = "-m reminders_sidecar"
npm run dev
```

That needs `pip install -e ./sidecar` in the venv once. Restart the app to pick
up a sidecar edit — the shell respawns it, no PyInstaller run involved.

> **Toasts will not appear under `npm run dev`.** Windows only delivers
> notifications for an application with a registered AppUserModelID, which is
> created by the installer. Test notifications from an installed build — this
> is a Windows constraint, not a bug in the app.

## Using it

First launch asks for your Apple ID and password, then a 2FA code, then walks
through a short setup: what stays running in the tray, notifications and
start-with-Windows, and the two things Apple will not allow — so they are known
up front rather than discovered later. The password goes to Windows Credential
Manager via `keyring`; the session persists, so subsequent launches skip both.

Closing the window hides it to the tray — the scheduler has to keep running for
due-date toasts to be worth anything. Quit from the tray menu.

## What it does and doesn't do

| | |
|---|---|
| Lists, reminders, detail pane | Read/write |
| Title, notes, due date, priority | Read/write |
| Tags | **Read and filter only** |
| Creating, renaming, deleting lists | **Not supported** |
| Background sync | Delta cursor, every 5–15 minutes |
| Due-date notifications | 30s tick, Windows toast |
| Smart lists | Today, Upcoming, All, Completed, Deleted |
| Printing | Any view, grouped by due date, priority or list |

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

**Printing.** Any view prints, including a search or a smart list. Rows are
grouped — by due date into Overdue / Today / Tomorrow / This week / This month /
Later, or by priority or list — and each gets an empty square to tick off by
hand. Notes and completed items are optional. `break-inside: avoid` keeps a
reminder from splitting across a page.

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

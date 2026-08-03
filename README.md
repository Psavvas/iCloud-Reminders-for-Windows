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
- **React + Vite frontend** — reconciled rather than rebuilt, so changing a
  filter re-renders only the rows that differ instead of discarding the list.
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
| `src-react/` (React UI) | `npm run build`, reinstall | hot-reloads instantly |
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
up front rather than discovered later.

**Staying signed in.** iCloud expires session tokens on its own schedule, every
few weeks. The password goes to Windows Credential Manager via `keyring`, which
is what lets the sidecar rebuild a session from the stored credential and the
trust token without a prompt: it does so on every launch, and again the moment a
background sync is refused. You see the sign-in screen when the restore actually
fails, not on a timer. Turn it off under Settings → Account if you would rather
type the password each time; signing out clears the stored credential either
way.

Closing the window hides it to the tray — the scheduler has to keep running for
due-date toasts to be worth anything. Quit from the tray menu.

## What it does and doesn't do

| | |
|---|---|
| Lists, reminders, detail pane | Read/write |
| Title, notes, due date, priority | Read/write |
| Tags | **Read and filter only** |
| Creating, renaming, deleting lists | **Not supported** |
| Background sync | Delta cursor, every 5–15 minutes, with progress and an ETA |
| Due-date notifications | 30s tick, Windows toast |
| Smart lists | Today, Upcoming, All, Completed, Deleted |
| Sorting | Per list: due date, priority, title, recently added |
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

**Times.** A reminder due "3 August at 8 PM" is a wall-clock fact, not a point
on the timeline, and Apple stores it as one: the CloudKit timestamp holds those
wall-clock fields encoded as though the zone were UTC, with a separate `TimeZone`
field naming the zone they belong to (null means the reminder floats). Reading
that timestamp as a real instant is wrong by the local UTC offset, and wrong in
the direction that makes things look overdue early west of Greenwich — an
all-day reminder, stored as midnight, renders as 8:00 PM the previous evening at
UTC-4 and lands under Overdue a full day ahead of time.

`sidecar/reminders_sidecar/timeutil.py` is the only place the two
representations meet; everything downstream works in true UTC instants, and the
cache rejects naive datetimes outright. Writes go through the same conversion in
reverse, so a due date set here shows the same time on the phone. The zone comes
from `tzlocal` rather than the current UTC offset, because a fixed offset taken
today converts a January date an hour wrong.

`scripts\check-due-dates.py` prints both readings side by side against a live
account, if that ever needs re-confirming.

**All-day reminders.** They have a date and no time, so they show as *All Day*
rather than midnight, go late only once their day is over, and toast at 9am —
a notification fired at 00:00 is one nobody reads.

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

**Ordering happens in SQLite.** Sort mode and the Completed cap are applied by
the query, not by JavaScript after the fact — a 1,219-row list should never
reach the UI just to be reordered or truncated there. Sort is remembered per
list, so ordering Chores by due date leaves every other list alone.

**Completed shows 50.** The full history runs to thousands of rows on a real
account and nobody scrolls it. Fifty most-recently-completed, newest first.

**Date headings.** Views spanning more than one day (Upcoming, All, Today)
group under Overdue / Today / Tomorrow / a real date, and rows under a heading
show only their time, since the date is already above them. Overdue and No Date
are exceptions — they span many days, so a bare time under them would read as
today. Grouping is by local calendar day, matching how the sidecar buckets Today.

**Sync progress.** The bar is weighted by each list's reminder count rather than
counting lists, because the work is wildly uneven — one list on the account this
was built against holds 1,219 of 2,900 records, and a bar advancing a
fourteenth per list would sit near the end for most of the sync. The estimate is
withheld below 8% and in the first few seconds, where extrapolating swings by
minutes between ticks. A delta sync has no knowable size, so it animates rather
than claiming a figure.

**Lists on every sync.** `iter_changes()` only ever reports reminders, so a
renamed or deleted list would never appear through the delta cursor. Lists are
re-read in full on every sync pass, delta included.

## Layout

```
sidecar/        Python: iCloud client, SQLite cache, sync, notification policy
src-tauri/      Rust: window, tray, timers, sidecar IPC
src-react/      React UI, built by Vite into dist/
scripts/        Icon generation, sidecar freeze, build checks
tools/          Screenshot and print-layout tooling
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

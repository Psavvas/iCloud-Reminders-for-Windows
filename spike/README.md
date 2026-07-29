# Phase 1 validation spike

Throwaway scripts that prove or disprove the seven Phase 1 items against a real
iCloud account. **These must be run on your Windows machine** — see
"Why you have to run this" below.

## Run it

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r spike\requirements.txt

# Seeds the keyring + does the interactive 2FA dance once.
icloud auth login --username you@example.com

python spike\run_spike.py --apple-id you@example.com
```

Options:

| Flag | Effect |
|------|--------|
| `--list "Name"` | Use a specific existing list for the write tests. Default: first non-group list. |
| `--accept-terms` | Accept updated iCloud T&Cs if Apple is blocking login on them. |
| `--no-confirm` | Skip the "check your iPhone" prompts. Items 3–6 then report **UNVERIFIED**, never PASS. |

Results print as a table and are written to `spike/results.md`.

## What it does to your account

Everything it creates is prefixed `ZZSPIKE` and deleted in the cleanup phase:

- 3 reminders (create test, tag test, delta test)
- 1 hashtag (`spiketag`)
- 1 list (`ZZSPIKE new list`) — **only if item 6's raw create is accepted**

If the run crashes midway, search Reminders for `ZZSPIKE` and delete leftovers by
hand. Note that reminder deletion in this API is a *soft* delete (`Deleted=1`),
which is why cleanup checks list membership rather than trusting a 404.

## Why you have to run this

I could not execute these checks myself:

1. **The sandbox has no route to Apple.** `setup.icloud.com` and
   `idmsa.apple.com` both return `403` at the CONNECT stage of the egress proxy.
2. **2FA needs your device**, and I shouldn't be handling your Apple ID password
   or 6-digit codes.
3. **Items 3–6 require visual confirmation on your iPhone** — whether a tag
   renders as a real tag chip rather than literal `#text` is precisely the thing
   an API response cannot tell you.

Items 6 and 7 were nonetheless answered from the library source; see the notes
below and in `list_create_experiment.py`.

## Findings that change Phase 2 design

**List colour is a JSON blob, not a hex string.** `RemindersList.color` comes
back as e.g.

```json
{"daSymbolicColorName":"custom","daHexString":"#5AC8FA","alpha":1,
 "red":0.352,"green":0.784,"blue":0.980,"colorRGBSpace":2,
 "ckSymbolicColorName":"lightBlue"}
```

The UI must parse this and use `daHexString`. Group rows (`is_group=True`) can
have `color=None`, so the sidebar needs a fallback.

**The account is bigger than a toy.** Observed live: 14 lists, ~2,900 reminders,
with single lists at 1219, 817 and 326. Initial sync is a few thousand records,
not a few dozen. Paging does work — an 818-reminder list read back completely
despite `results_limit=200` — but the first sync should be treated as a bulk
load with progress, and the SQLite cache is doing real work rather than being a
nicety.

**Text fields are written wrong by pyicloud.** `_encode_cloudkit_text_field`
emits `{"type": "ENCRYPTED_BYTES", "value": base64(text)}`, but Apple stores
these as plain `STRING`. Apple accepts the byte form silently, so writes look
successful while the Reminders UI cannot read them. Confirmed three ways on a
live account: all 14 `List` records store `Name` as `STRING`; a hashtag created
on the iPhone stores `Name` as `STRING` (plus `Type` and `Imported` INT64
fields); a hashtag created by pyicloud stores `Name` as `ENCRYPTED_BYTES`. Any
sidecar write touching a text field needs a corrected layer over the library.

**The phone discards malformed writes rather than merging.** After adding a tag
by hand on the iPhone, `Reminder.HashtagIDs` went from `['ADFDF21B…']` to
`['1B27D7B2…']` — our record was dropped, not kept alongside. Conflict handling
cannot assume our writes survive; the app has to read back and reconcile.

**Tags are not in the CRDT title document.** `TitleDocument` was byte-identical
(113 bytes) before and after a hand-typed tag. Tags are `Hashtag` records plus
`Reminder.HashtagIDs`, nothing more.

## Notes per item

**1 — Auth/persistence.** The restart test is real: `run_spike.py` spawns
`session_probe.py` as a separate OS process with no password and no 2FA path. If
that process has to re-authenticate, the check fails.

**3 — Due dates.** Deliberately uses a tz-aware `datetime` in a non-UTC zone and
asserts the value comes back tz-aware and within 60s. pyicloud's `create()`
docstring confirms your landmine: *"Naive datetimes are treated as UTC."*

**5 — Tags.** `update_hashtag()` is deliberately not exercised. Upstream's own
docstring warns hashtag names are "effectively read-only in some live flows",
which matches your note — rename should be delete + create.

**6 — List creation.** There is **no list-creation method anywhere in pyicloud**
(`lists()` is read-only; a package-wide grep for a create/add/new list method
returns nothing). So the script drops to the raw CloudKit `modify` endpoint and
tries to create a `List` record directly, with three escalating field shapes. It
records Apple's verbatim `serverErrorCode` on rejection. This is a hypothesis
test — "not possible" is a legitimate result and the script reports it as such.

**7 — Delta sync.** `iter_changes()` filters to `recordType == "Reminder"` and
skips everything else. **List create/rename/delete will not appear in the delta
stream** — Phase 2 needs a periodic full `lists()` refresh regardless of cursor
state. The check also verifies a second run from a fresh cursor does not replay
the change the first run already consumed.

# Security audit

Date: 2026-08-09

## Scope and assurance

This review covers the native WinUI process, its newline-delimited JSON boundary, the
native Rust iCloud connector, local SQLite state, Windows credential storage,
build scripts, and CI packaging. The connector implements only the operations
the application calls: authentication and trusted-session restoration, 2FA,
list/reminder/tag reads, reminder create/update/soft-delete/restore, delta/full
sync, conflict handling, settings, and due notifications.

This is a source review, not a claim that Apple's private Reminders protocol is
stable or officially supported. Validation was performed on Windows: the
account-free Rust tests, release sidecar protocol smoke test, x64 native publish,
and x64 MSIX assembly passed. GitHub Actions also compiled and packaged the x64
and ARM64 variants; only artifact upload failed because the private repository's
storage quota was full. No live Apple account was used during this review.

## Security boundaries

- The WinUI client has no scripting runtime or web content. The sidecar still
  validates method names, field sizes, record types, and CloudKit operation counts.
- The WinUI process launches one bundled sidecar and communicates over inherited
  stdin/stdout. No local TCP listener is opened.
- Credentials and serialized session tokens are stored only in Windows
  Credential Manager. They are not written to SQLite or protocol logs.
- Reminder content, tags, settings, outbox entries, and conflict snapshots are
  cached in SQLite under the current user's application-data directory.
- Network traffic is HTTPS-only, ignores ambient proxy configuration, rejects
  redirects, and restricts Apple-provided CloudKit service URLs to `icloud.com`
  hosts.

## Findings fixed during the rewrite

### Release sidecar path substitution (high)

Release builds previously searched environment and working-directory paths for
the sidecar. A local file in a writable directory could therefore be selected
instead of the installed connector. Release builds now consider only the
application resource directory and executable directory. Development overrides
are compiled only in debug builds, and the child working directory is pinned to
the selected executable's directory.

### Apple service URL used without validation (high)

An authenticated response supplies the CloudKit base URL. Following it without
validation would turn a compromised or malformed session response into an SSRF
and token-disclosure primitive. The connector now requires HTTPS, no embedded
credentials, and an exact `icloud.com` host or subdomain before attaching auth
headers.

### Sensitive diagnostics crossing process boundaries (medium)

Malformed sidecar output and raw Apple error bodies could contain reminder or
session data. The client does not log the offending protocol line. Authentication
and non-conflict CloudKit failures expose only selected, length-limited public
error fields. Conflict records intentionally retain the conflicting reminder
values because the UI needs them for resolution.

### Unbounded remote work and responses (medium)

HTTP requests now have connection and overall timeouts. Response bodies are
read incrementally and rejected above 2 MiB. CloudKit query/lookup/modify batch
sizes are bounded, SRP iteration/salt/public-value parameters are validated,
and UI-controlled text fields have explicit limits.

### Credential copies (low)

Passwords are held in zeroizing memory and passed to the SRP implementation by
reference, avoiding an extra ordinary `String` copy. Persistent password and
session state uses the Windows-native credential backend.

## Residual risks and release blockers

0. **Two-factor sign-in is a functional regression against the Python sidecar,
   and it is shipping that way deliberately.** Apple verifies trusted-device
   prompts through its HSA2 bridge; pyicloud implements that in roughly 2,300
   lines, so `main` supported device prompts by inheriting it from a dependency.
   This connector implements only the two plain-HTTP verifiers, so a **texted
   code is the only route that completes a sign-in**, and an Apple ID with no
   trusted phone number cannot sign in at all.

   This is documented in the README, the getting-started guide, and the release
   notes, and the app reports the specific `409` as a wrong route rather than a
   wrong code. Anyone migrating from a `main` build should be told before they
   upgrade. See `docs/protocol-findings.md` for what porting the bridge
   involves.

1. **Live-account validation is still required.** 2FA, trust, terms acceptance,
   CloudKit record shapes, conflict behavior, and all due-date timezone cases
   must be exercised with a dedicated test account. Start with read-only sync;
   test mutations only on disposable reminders and lists.

   The SRP cryptography itself is no longer unverified: `sidecar/src/auth.rs`
   now asserts M1, M2 and the password derivation against vectors generated
   from pyicloud + the `srp` package, which is the stack the previous Python
   sidecar used against Apple in production. See
   `docs/srp-reference-vectors.md`. What remains untested is the flow around
   it — cookie and header capture, trust tokens, session restore.
2. **SRP salts beginning with `0x00` are an open question.** pyicloud routes the
   salt through an OpenSSL BIGNUM and so strips leading zero bytes; this
   implementation hashes the salt verbatim. The two agree for every other salt.
   Roughly one account in 256 would be affected, and because the salt is fixed
   when the password is set, such an account fails sign-in *every* time rather
   than intermittently — it looks exactly like a wrong password. Settle this
   against a disposable account before release; do not guess a fix.
3. **Audit the locked Rust dependency graph.** `sidecar/Cargo.lock` is committed
   and the account-free tests pass. CI now runs `cargo audit` and
   `cargo clippy -D warnings` on every build; keep reviewing lockfile changes
   like source changes.
4. **The local reminder cache is plaintext.** Windows user ACLs protect the app
   data directory, but another process running as the same user can read it.
   Credentials and tokens are not in this database. Encrypting reminder content
   would require a separate product decision covering key lifecycle, search,
   migration, recovery, and crash consistency.
5. **Apple's web protocol is private and can drift.** Client build constants,
   endpoints, and record encodings may change without notice. Authentication
   failures must fail closed; do not add fallback endpoints or relax the Apple
   hostname check to restore compatibility.
6. **The protocol reader is now bounded before allocation.** `main.rs` caps
   stdin with `take`, so an over-long line is refused rather than buffered. The
   only writer is the parent WinUI process, so a future framed transport can
   enforce the cap before allocation.
7. **Independent review is outstanding.** This implementation and audit were
   produced in the same change set. A second reviewer should inspect SRP math,
   CloudKit mutations, credential lifecycle, and installer path selection.

## Release verification

Run these on a disposable Windows VM or CI runner. Items 1 and 2 now run in CI
on every build; item 3 does too.

1. `cargo test --manifest-path .\sidecar\Cargo.toml --locked`
2. `cargo clippy --manifest-path .\sidecar\Cargo.toml --all-targets -- -D warnings`
3. `cargo audit --file .\sidecar\Cargo.lock`
4. `.\scripts\build-sidecar.ps1` and verify its JSON protocol smoke test.
5. Run `.\scripts\build-windows.ps1` and verify `Reminders.exe` and the tested
   `reminders-sidecar.exe` are both present in `dist-windows`.
6. Confirm credentials and session tokens appear only in Windows Credential
   Manager and never in `cache.db`, logs, crash output, or protocol captures.
7. Exercise sign-in failure, 2FA rejection/success, trusted restart, sign-out,
   expired-session recovery, and terms-required failure.
8. Compare full and delta sync with the Windows UI and an Apple device, then
   test create/edit/complete/flag/delete/restore/conflict on disposable data.
9. Test timed and all-day reminders across DST boundaries and in at least two
   Windows time zones.

## Conclusion

No known critical vulnerability remains in the reviewed source. The rewrite
materially reduces third-party runtime trust and does not ship a scripting
runtime. A production release still requires a locked-dependency vulnerability
scan, the remaining Windows checks above, and disposable-account
interoperability testing.

# iCloud Reminders protocol findings

These observations came from the repository's original live-account probes and
define the behavior the native Rust connector must preserve.

- Reminders use the private `com.apple.reminders` CloudKit container and the
  `Reminders` custom zone. The API is undocumented and may change.
- Due dates are wall-clock fields encoded in a CloudKit timestamp as though the
  fields were UTC. `TimeZone` optionally anchors them; a missing zone floats in
  the device's current zone. Creation, modification, and completion dates are
  ordinary instants.
- Deletion is a soft `Deleted = 1` field update.
- List changes do not appear in the reminder delta stream, so every delta pass
  also refreshes lists.
- List colors are JSON blobs; `daHexString` is the preferred display value.
- Tags are readable but writes accepted by CloudKit were not rendered by Apple
  clients. This app therefore keeps tags read-only.
- List creates, renames, and deletes accepted by CloudKit did not propagate to
  Apple devices. This app does not expose those operations.
- A phone may replace linked field collections wholesale. Local writes carry a
  base `recordChangeTag`; mismatches become user-visible conflicts rather than
  last-write-wins overwrites.

## Two-factor sign-in

- `signin/complete` answers **409** when Apple wants a second factor. That
  response also carries the challenge options: `trustedDeviceCount` and
  `trustedPhoneNumbers`. Keep them -- they decide where a code can be sent, and
  the challenge is thrown away by the next sign-in attempt.
- **Apple rotates `scnt` on every response from the auth endpoint** and refuses
  any later request that echoes a stale one. Every `idmsa` response must be read
  for a new `scnt`, `X-Apple-ID-Session-Id` and `X-Apple-Session-Token`,
  including the ones for requesting and submitting a code. A stale `scnt` is
  refused in a way that is indistinguishable from a wrong code.
- There are two delivery routes and they do not interoperate. A code pushed to a
  trusted device is requested with `GET verify/trusteddevice` and validated at
  `POST verify/trusteddevice/securitycode`; a texted code is requested with
  `PUT verify/phone` and validated at `POST verify/phone/securitycode`. Sending
  on one route and validating on the other is rejected, again exactly as a wrong
  code is. An account with no trusted device only has the phone route.
- **Both delivery endpoints answer with a non-2xx status and send the code
  anyway.** Observed on a live account: the app reported "Apple would not send a
  verification code" while the prompt was on the user's phone and two texts had
  arrived. The session is still unauthenticated at that point and Apple says so;
  that is not a refusal. Never infer "no code was sent" from the status of these
  two requests -- only a dead session (401/403/421) is a real failure.
- Only `service_errors[0].code == "-21669"` means the digits were wrong. Every
  other refusal is ambiguous -- it can equally mean the code was fine and the
  challenge or the route was not -- so it must not be reported as a typing
  mistake, and it is the signal that another route is worth trying.
- Apple's own `service_errors[0].message` is user-grade text ("Incorrect
  verification code.", "Enter the verification code displayed on your other
  devices."). Prefer it over anything inferred from a status code.
- Requesting a *text* mints a new code and retires the previous one, so a text
  must only ever follow the user asking for one. Re-requesting the trusted-device
  push re-serves the same challenge and is safe.
- Whether the 409 pushes the device code on its own is not observable from the
  client, so the app requests one and tracks which routes have actually
  delivered. That also means a user can legitimately hold two live codes at
  once, and verification tries every route that sent one before blaming the
  typing.
- Rebuilding the session re-runs SRP and voids any outstanding challenge. The
  background sync must not do that while a code is in flight.
- The exact statuses Apple returns here are undocumented and were guessed wrong
  once already. The sidecar logs them (`2fa: ... returned HTTP ...`) to stderr,
  which the app captures into
  `%LOCALAPPDATA%\RemindersSync\logs\app.log` -- read that before theorising.

The authentication and record formats need live Windows/account validation
whenever Apple changes the private service. Tests must use a dedicated account
and must never log passwords, verification codes, session tokens, record
contents, or credential-vault values.

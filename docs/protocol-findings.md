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
- A genuinely mistyped code comes back as `serviceErrors[0].code == "-21669"`.
  Other failures mean the challenge itself is no longer usable, and the only way
  forward is a fresh code -- so they must not be reported as a typing mistake.
- Re-requesting a code retires the previous one. Ask exactly once per challenge,
  and only again when the user asks for a new code.
- Rebuilding the session re-runs SRP and voids any outstanding challenge. The
  background sync must not do that while a code is in flight.

The authentication and record formats need live Windows/account validation
whenever Apple changes the private service. Tests must use a dedicated account
and must never log passwords, verification codes, session tokens, record
contents, or credential-vault values.

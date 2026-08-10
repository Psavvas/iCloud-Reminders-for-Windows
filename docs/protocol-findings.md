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

The authentication and record formats need live Windows/account validation
whenever Apple changes the private service. Tests must use a dedicated account
and must never log passwords, verification codes, session tokens, record
contents, or credential-vault values.

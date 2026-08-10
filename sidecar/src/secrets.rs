//! Secrets live in the operating-system credential vault, never in SQLite or
//! the sidecar data directory. On Windows the selected keyring backend is the
//! native Credential Manager implementation.

use zeroize::Zeroizing;

use crate::error::{AppError, Result};

const PASSWORD_SERVICE: &str = "com.cdenihan.reminders-sync.password";
const SESSION_SERVICE: &str = "com.cdenihan.reminders-sync.session";
const SESSION_CHUNK_SERVICE: &str = "com.cdenihan.reminders-sync.session.chunk";
// Windows Credential Manager accepts at most 2,560 bytes per generic
// credential. keyring stores passwords as UTF-16, so stay comfortably below
// the 1,280-code-unit hard limit.
const SESSION_CHUNK_UTF16_UNITS: usize = 1_000;
const MAX_SESSION_CHUNKS: usize = 64;

#[cfg(windows)]
fn entry(service: &str, apple_id: &str) -> Result<keyring::Entry> {
    keyring::Entry::new(service, apple_id).map_err(|e| {
        AppError::internal("Could not open the system credential vault", e.to_string())
    })
}

fn session_chunks(value: &str) -> Vec<&str> {
    if value.is_empty() {
        return vec![""];
    }
    let mut chunks = Vec::new();
    let mut start = 0;
    let mut units = 0;
    for (index, character) in value.char_indices() {
        let character_units = character.len_utf16();
        if units + character_units > SESSION_CHUNK_UTF16_UNITS {
            chunks.push(&value[start..index]);
            start = index;
            units = 0;
        }
        units += character_units;
    }
    chunks.push(&value[start..]);
    chunks
}

fn session_manifest(slot: char, count: usize) -> String {
    format!("chunks:v1:{slot}:{count}")
}

fn parse_session_manifest(value: &str) -> Option<(char, usize)> {
    let mut parts = value.split(':');
    if parts.next()? != "chunks" || parts.next()? != "v1" {
        return None;
    }
    let slot = parts.next()?.chars().next()?;
    if !matches!(slot, 'a' | 'b') {
        return None;
    }
    let count = parts.next()?.parse().ok()?;
    if parts.next().is_some() || count == 0 || count > MAX_SESSION_CHUNKS {
        return None;
    }
    Some((slot, count))
}

#[cfg(windows)]
fn session_chunk_service(slot: char, index: usize) -> String {
    format!("{SESSION_CHUNK_SERVICE}.{slot}.{index:02}")
}

#[cfg(windows)]
fn delete_entry(service: &str, apple_id: &str) -> Result<()> {
    match entry(service, apple_id)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(AppError::internal(
            "Could not remove a saved iCloud session",
            error.to_string(),
        )),
    }
}

#[cfg(windows)]
pub fn set_password(apple_id: &str, password: &str) -> Result<()> {
    entry(PASSWORD_SERVICE, apple_id)?
        .set_password(password)
        .map_err(|e| AppError::internal("Could not save the password securely", e.to_string()))
}

#[cfg(not(windows))]
pub fn set_password(_apple_id: &str, _password: &str) -> Result<()> {
    Err(AppError::internal(
        "Credential persistence is available only on Windows",
        "",
    ))
}

#[cfg(windows)]
pub fn password(apple_id: &str) -> Result<Option<Zeroizing<String>>> {
    match entry(PASSWORD_SERVICE, apple_id)?.get_password() {
        Ok(value) => Ok(Some(Zeroizing::new(value))),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(AppError::internal(
            "Could not read the saved password",
            e.to_string(),
        )),
    }
}

#[cfg(not(windows))]
pub fn password(_apple_id: &str) -> Result<Option<Zeroizing<String>>> {
    Ok(None)
}

pub fn has_password(apple_id: &str) -> bool {
    password(apple_id).ok().flatten().is_some()
}

#[cfg(windows)]
pub fn delete_password(apple_id: &str) -> Result<()> {
    match entry(PASSWORD_SERVICE, apple_id)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(AppError::internal(
            "Could not remove the saved password",
            e.to_string(),
        )),
    }
}

#[cfg(not(windows))]
pub fn delete_password(_apple_id: &str) -> Result<()> {
    Ok(())
}

#[cfg(windows)]
pub fn set_session(apple_id: &str, value: &str) -> Result<()> {
    let chunks = session_chunks(value);
    if chunks.len() > MAX_SESSION_CHUNKS {
        return Err(AppError::internal(
            "Could not save the iCloud session",
            "The session is unexpectedly large",
        ));
    }

    // Alternate slots so the old manifest remains usable until every new
    // chunk has been written. This avoids a partially updated session if
    // Credential Manager rejects or interrupts one of the writes.
    let previous = match entry(SESSION_SERVICE, apple_id)?.get_password() {
        Ok(value) => parse_session_manifest(&value),
        Err(keyring::Error::NoEntry) => None,
        Err(error) => {
            return Err(AppError::internal(
                "Could not read the saved iCloud session",
                error.to_string(),
            ));
        }
    };
    let slot = if previous.is_some_and(|(slot, _)| slot == 'a') {
        'b'
    } else {
        'a'
    };

    for (index, chunk) in chunks.iter().enumerate() {
        entry(&session_chunk_service(slot, index), apple_id)?
            .set_password(chunk)
            .map_err(|error| {
                AppError::internal("Could not save the iCloud session", error.to_string())
            })?;
    }
    entry(SESSION_SERVICE, apple_id)?
        .set_password(&session_manifest(slot, chunks.len()))
        .map_err(|error| {
            AppError::internal("Could not save the iCloud session", error.to_string())
        })?;

    if let Some((old_slot, old_count)) = previous {
        for index in 0..old_count {
            let _ = delete_entry(&session_chunk_service(old_slot, index), apple_id);
        }
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn set_session(_apple_id: &str, _value: &str) -> Result<()> {
    Ok(())
}

#[cfg(windows)]
pub fn session(apple_id: &str) -> Result<Option<Zeroizing<String>>> {
    match entry(SESSION_SERVICE, apple_id)?.get_password() {
        Ok(value) => {
            let manifest = parse_session_manifest(&value);
            let legacy_or_manifest = Zeroizing::new(value);
            let Some((slot, count)) = manifest else {
                return Ok(Some(legacy_or_manifest));
            };
            let mut session = Zeroizing::new(String::new());
            for index in 0..count {
                let chunk = entry(&session_chunk_service(slot, index), apple_id)?
                    .get_password()
                    .map_err(|error| {
                        AppError::internal(
                            "Could not read the saved iCloud session",
                            error.to_string(),
                        )
                    })?;
                let chunk = Zeroizing::new(chunk);
                session.push_str(&chunk);
            }
            Ok(Some(session))
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(AppError::internal(
            "Could not read the saved iCloud session",
            e.to_string(),
        )),
    }
}

#[cfg(not(windows))]
pub fn session(_apple_id: &str) -> Result<Option<Zeroizing<String>>> {
    Ok(None)
}

#[cfg(windows)]
pub fn delete_session(apple_id: &str) -> Result<()> {
    delete_entry(SESSION_SERVICE, apple_id)?;
    // Clean either active slot plus any contiguous chunks left by an
    // interrupted pre-manifest write.
    for slot in ['a', 'b'] {
        for index in 0..MAX_SESSION_CHUNKS {
            let service = session_chunk_service(slot, index);
            match entry(&service, apple_id)?.delete_credential() {
                Ok(()) => {}
                Err(keyring::Error::NoEntry) => break,
                Err(error) => {
                    return Err(AppError::internal(
                        "Could not remove the saved iCloud session",
                        error.to_string(),
                    ));
                }
            }
        }
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn delete_session(_apple_id: &str) -> Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        SESSION_CHUNK_UTF16_UNITS, parse_session_manifest, session_chunks, session_manifest,
    };

    #[test]
    fn session_chunks_respect_windows_utf16_limit_and_round_trip() {
        let value = format!("{}{}{}", "a".repeat(999), "😀".repeat(20), "z".repeat(1_500));
        let chunks = session_chunks(&value);
        assert!(chunks.len() > 1);
        assert!(
            chunks
                .iter()
                .all(|chunk| chunk.encode_utf16().count() <= SESSION_CHUNK_UTF16_UNITS)
        );
        assert_eq!(chunks.concat(), value);
    }

    #[test]
    fn session_manifest_round_trips_and_rejects_invalid_values() {
        assert_eq!(parse_session_manifest(&session_manifest('b', 7)), Some(('b', 7)));
        assert_eq!(parse_session_manifest("not-a-manifest"), None);
        assert_eq!(parse_session_manifest("chunks:v1:x:2"), None);
        assert_eq!(parse_session_manifest("chunks:v1:a:0"), None);
    }
}

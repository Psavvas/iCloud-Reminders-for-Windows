//! Apple stores reminder due dates as wall-clock components disguised as a UTC
//! timestamp. All other timestamps are real instants. This module is the only
//! place that representation boundary is crossed.

use chrono::{DateTime, Duration, LocalResult, NaiveDate, NaiveDateTime, TimeZone, Utc};
use chrono_tz::Tz;

use crate::error::{AppError, Result};

pub const ALL_DAY_HOUR: u32 = 9;

pub fn local_zone() -> Tz {
    iana_time_zone::get_timezone()
        .ok()
        .and_then(|name| name.parse().ok())
        .unwrap_or(chrono_tz::UTC)
}

pub fn named_zone(name: Option<&str>) -> Option<Tz> {
    name.and_then(|value| value.parse().ok())
}

fn anchor(zone: Tz, wall: NaiveDateTime) -> DateTime<Tz> {
    match zone.from_local_datetime(&wall) {
        LocalResult::Single(value) => value,
        // During a fall-back overlap, choose the earlier instant. That matches
        // the first occurrence users see on the wall clock.
        LocalResult::Ambiguous(earlier, _) => earlier,
        // During a spring-forward gap there is no such wall time. Advancing to
        // the first valid minute avoids silently moving to the previous day.
        LocalResult::None => {
            let mut candidate = wall;
            for _ in 0..180 {
                candidate += Duration::minutes(1);
                if let LocalResult::Single(value) = zone.from_local_datetime(&candidate) {
                    return value;
                }
            }
            Utc.from_utc_datetime(&wall).with_timezone(&zone)
        }
    }
}

pub fn floating_millis_to_instant(raw_ms: i64, tz_name: Option<&str>) -> DateTime<Utc> {
    let disguised = DateTime::<Utc>::from_timestamp_millis(raw_ms).unwrap_or(DateTime::UNIX_EPOCH);
    let zone = named_zone(tz_name).unwrap_or_else(local_zone);
    anchor(zone, disguised.naive_utc()).with_timezone(&Utc)
}

pub fn instant_to_floating_millis(instant: DateTime<Utc>, tz_name: Option<&str>) -> i64 {
    let zone = named_zone(tz_name).unwrap_or_else(local_zone);
    let wall = instant.with_timezone(&zone).naive_local();
    Utc.from_utc_datetime(&wall).timestamp_millis()
}

pub fn parse_instant(value: &str) -> Result<DateTime<Utc>> {
    if let Ok(value) = DateTime::parse_from_rfc3339(value) {
        return Ok(value.with_timezone(&Utc));
    }
    let wall = NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S")
        .or_else(|_| NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M"))
        .or_else(|_| {
            NaiveDate::parse_from_str(value, "%Y-%m-%d")
                .map(|date| date.and_hms_opt(0, 0, 0).expect("midnight is valid"))
        })
        .map_err(|e| AppError::bad_request(format!("invalid due date: {e}")))?;
    Ok(anchor(local_zone(), wall).with_timezone(&Utc))
}

pub fn to_iso(value: DateTime<Utc>) -> String {
    value.to_rfc3339_opts(chrono::SecondsFormat::Micros, true)
}

pub fn notify_at(due: DateTime<Utc>, all_day: bool) -> DateTime<Utc> {
    if !all_day {
        return due;
    }
    let zone = local_zone();
    let local = due.with_timezone(&zone);
    let wall = local
        .date_naive()
        .and_hms_opt(ALL_DAY_HOUR, 0, 0)
        .expect("9am is a valid wall time");
    anchor(zone, wall).with_timezone(&Utc)
}

pub fn local_day_bounds(now: DateTime<Utc>) -> (DateTime<Utc>, DateTime<Utc>) {
    let zone = local_zone();
    let local = now.with_timezone(&zone);
    let today = local.date_naive();
    let tomorrow = today.succ_opt().unwrap_or(today);
    (
        anchor(zone, today.and_hms_opt(0, 0, 0).expect("midnight is valid")).with_timezone(&Utc),
        anchor(
            zone,
            tomorrow.and_hms_opt(0, 0, 0).expect("midnight is valid"),
        )
        .with_timezone(&Utc),
    )
}

pub fn normalize_due(value: &serde_json::Value, all_day: bool) -> Result<Option<String>> {
    if value.is_null() || value.as_str() == Some("") {
        return Ok(None);
    }
    let mut instant =
        if let Some(ms) = value.as_i64() {
            DateTime::<Utc>::from_timestamp_millis(ms)
                .ok_or_else(|| AppError::bad_request("due date is outside the supported range"))?
        } else {
            parse_instant(value.as_str().ok_or_else(|| {
                AppError::bad_request("due date must be ISO-8601 or epoch millis")
            })?)?
        };
    if all_day {
        let zone = local_zone();
        let local = instant.with_timezone(&zone);
        instant = anchor(
            zone,
            local
                .date_naive()
                .and_hms_opt(0, 0, 0)
                .expect("midnight is valid"),
        )
        .with_timezone(&Utc);
    }
    Ok(Some(to_iso(instant)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;

    #[test]
    fn new_york_wall_clock_round_trips_across_dst() {
        let instant = DateTime::parse_from_rfc3339("2026-07-10T00:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let encoded = instant_to_floating_millis(instant, Some("America/New_York"));
        assert_eq!(
            floating_millis_to_instant(encoded, Some("America/New_York")),
            instant
        );
    }

    #[test]
    fn all_day_alerts_at_nine() {
        let due = DateTime::parse_from_rfc3339("2026-07-10T04:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let alert = notify_at(due, true).with_timezone(&local_zone());
        assert_eq!((alert.hour(), alert.minute()), (9, 0));
    }
}

use chrono::{DateTime, Duration, Utc};
use serde::Serialize;

use crate::cache::Cache;
use crate::error::Result;
use crate::timeutil::{local_zone, notify_at, parse_instant, to_iso};

#[derive(Debug, Serialize)]
pub struct Toast {
    pub title: String,
    pub body: String,
    pub reminder_id: Option<String>,
    pub kind: &'static str,
}

#[derive(Debug, Default, Serialize)]
pub struct NotificationPlan {
    pub toasts: Vec<Toast>,
    pub notified_ids: Vec<String>,
}

pub fn plan(
    cache: &Cache,
    now: DateTime<Utc>,
    stale_after_minutes: i64,
    max_individual: usize,
) -> Result<NotificationPlan> {
    let due = cache.due_unnotified(&to_iso(now))?;
    let cutoff = now - Duration::minutes(stale_after_minutes.clamp(1, 24 * 60));
    let mut missed = Vec::new();
    let mut fresh = Vec::new();
    for reminder in due {
        let Some(raw_due) = reminder.due_date.as_deref() else {
            continue;
        };
        let alert = notify_at(parse_instant(raw_due)?, reminder.all_day);
        if alert > now {
            continue;
        }
        if alert < cutoff {
            missed.push(reminder);
        } else {
            fresh.push(reminder);
        }
    }

    let mut result = NotificationPlan::default();
    if !missed.is_empty() {
        let mut names: Vec<_> = missed
            .iter()
            .take(3)
            .filter(|r| !r.title.is_empty())
            .map(|r| r.title.clone())
            .collect();
        if missed.len() > 3 {
            names.push(format!("and {} more", missed.len() - 3));
        }
        result.toasts.push(Toast {
            title: format!(
                "{} reminder{} were due while you were away",
                missed.len(),
                if missed.len() == 1 { "" } else { "s" }
            ),
            body: if names.is_empty() {
                "Open Reminders to review them.".into()
            } else {
                names.join(", ")
            },
            reminder_id: None,
            kind: "summary",
        });
        result
            .notified_ids
            .extend(missed.iter().map(|r| r.id.clone()));
    }

    if !fresh.is_empty() {
        if fresh.len() <= max_individual.clamp(1, 10) {
            for reminder in &fresh {
                let due = reminder
                    .due_date
                    .as_deref()
                    .map(parse_instant)
                    .transpose()?;
                let body = due
                    .map(|value| {
                        let local = value.with_timezone(&local_zone());
                        if reminder.all_day {
                            local.format("%a %d %b").to_string()
                        } else {
                            local.format("%a %d %b, %H:%M").to_string()
                        }
                    })
                    .unwrap_or_else(|| "Due now".into());
                result.toasts.push(Toast {
                    title: if reminder.title.is_empty() {
                        "Reminder".into()
                    } else {
                        reminder.title.clone()
                    },
                    body,
                    reminder_id: Some(reminder.id.clone()),
                    kind: "reminder",
                });
            }
        } else {
            result.toasts.push(Toast {
                title: format!("{} reminders are due", fresh.len()),
                body: fresh
                    .iter()
                    .take(3)
                    .map(|r| r.title.as_str())
                    .filter(|s| !s.is_empty())
                    .collect::<Vec<_>>()
                    .join(", "),
                reminder_id: None,
                kind: "summary",
            });
        }
        result
            .notified_ids
            .extend(fresh.iter().map(|r| r.id.clone()));
    }

    cache.mark_notified(&result.notified_ids)?;
    Ok(result)
}

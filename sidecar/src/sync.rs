use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use chrono::Utc;
use serde_json::{Value, json};
use tokio::sync::{Mutex, mpsc::UnboundedSender};

use crate::cache::Cache;
use crate::error::{AppError, Result};
use crate::icloud::ICloudClient;
use crate::model::Reminder;
use crate::timeutil::to_iso;

pub const CURSOR_KEY: &str = "sync_cursor";
pub const LAST_FULL_KEY: &str = "last_full_sync";
pub const LAST_SYNC_KEY: &str = "last_sync";
const READ_PROTOCOL_KEY: &str = "cloudkit_read_protocol";
const READ_PROTOCOL_VERSION: &str = "2";

pub struct SyncEngine {
    cache: Arc<Cache>,
    client: Arc<Mutex<ICloudClient>>,
    events: UnboundedSender<Value>,
    running: AtomicBool,
    pushing: Mutex<()>,
}

struct RunningGuard<'a>(&'a AtomicBool);
impl Drop for RunningGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

impl SyncEngine {
    pub fn new(
        cache: Arc<Cache>,
        client: Arc<Mutex<ICloudClient>>,
        events: UnboundedSender<Value>,
    ) -> Self {
        Self {
            cache,
            client,
            events,
            running: AtomicBool::new(false),
            pushing: Mutex::new(()),
        }
    }
    pub fn running(&self) -> bool {
        self.running.load(Ordering::Acquire)
    }
    fn emit(&self, event: &str, data: Value) {
        let _ = self.events.send(json!({"event":event,"data":data}));
    }

    pub async fn sync_now(&self, full: bool) -> Result<Value> {
        if self.running.swap(true, Ordering::AcqRel) {
            return Ok(json!({"queued":false,"reason":"already running"}));
        }
        let _guard = RunningGuard(&self.running);
        let push = self.push_with_reauth().await?;
        let pull = self.pull_with_reauth(full).await?;
        Ok(json!({"push":push,"pull":pull}))
    }

    pub async fn push_now(&self) -> Result<Value> {
        self.push_with_reauth().await
    }

    /// Rebuild the session once after Apple refuses a request, or explain why not.
    ///
    /// The old version treated every failure to get back in as an expiry, and
    /// announced it as one. That is what "it signs me out all the time" was: the
    /// sticky sign-in notice went up over a dropped connection, and the only
    /// thing that clears that notice is signing in -- so a ten-second blip cost
    /// a password.
    async fn recover_session(&self) -> Result<()> {
        let outcome = {
            let mut client = self.client.lock().await;
            if client.awaiting_2fa() {
                // Someone is at the code box. Rebuilding from here hands Apple a
                // new challenge and retires the code they are halfway through
                // typing, which is how a correct code ends up rejected.
                return Err(AppError::TwoFactorRequired {
                    message: "Enter the verification code to finish signing in.".into(),
                    detail: String::new(),
                });
            }
            client.invalidate();
            let restored = client.restore().await;
            (restored, client.restore_is_retryable(), client.restore_detail())
        };
        let (restored, retryable, detail) = outcome;
        if restored {
            self.emit("auth_changed", self.client.lock().await.status());
            return Ok(());
        }
        if retryable {
            // Nothing here established that the session is gone, so do not say
            // that it is. The next pass tries again.
            return Err(AppError::Network {
                message: "Couldn't reach iCloud. Sync will try again shortly.".into(),
                detail,
            });
        }
        self.emit("auth_changed", self.client.lock().await.status());
        Err(AppError::AuthRequired {
            message: "Your iCloud session expired. Please sign in again.".into(),
            detail: String::new(),
        })
    }

    async fn push_with_reauth(&self) -> Result<Value> {
        match self.flush_outbox().await {
            Err(AppError::AuthRequired { .. }) => {
                self.recover_session().await?;
                self.flush_outbox().await
            }
            result => result,
        }
    }

    async fn pull_with_reauth(&self, full: bool) -> Result<Value> {
        // Protocol v1 could persist a valid cursor after Apple's unsupported
        // raw record queries returned an empty snapshot. Force one full pull
        // after upgrading so that stale empty state cannot survive the fix.
        let needs_full = full
            || self.cache.get_meta(CURSOR_KEY)?.is_none()
            || self.cache.get_meta(READ_PROTOCOL_KEY)?.as_deref()
                != Some(READ_PROTOCOL_VERSION);
        let first = if needs_full {
            self.full_sync().await
        } else {
            self.delta_sync().await
        };
        match first {
            Err(AppError::AuthRequired { .. }) => {
                self.recover_session().await?;
                if needs_full {
                    self.full_sync().await
                } else {
                    self.delta_sync().await
                }
            }
            result => result,
        }
    }

    async fn full_sync(&self) -> Result<Value> {
        self.emit("sync_started", json!({"mode":"full","determinate":true}));
        let cursor = self.client.lock().await.sync_cursor().await?;
        let lists = self.client.lock().await.lists().await?;
        self.cache.replace_lists(&lists)?;
        let work: Vec<_> = lists.iter().filter(|list| !list.is_group).collect();
        let weights: Vec<i64> = work.iter().map(|list| list.count.max(1)).collect();
        let budget = weights.iter().sum::<i64>().max(1);
        self.emit("sync_progress",json!({"stage":"lists","count":lists.len(),"expected":work.iter().map(|l|l.count).sum::<i64>(),"percent":0.0,"done":0,"of":work.len()}));
        let mut total = 0usize;
        let mut spent = 0i64;
        let mut reminder_ids = Vec::new();
        for (index, list) in work.iter().enumerate() {
            let reminders = self.client.lock().await.reminders_for(&list.id).await?;
            self.cache.upsert_reminders(&reminders)?;
            reminder_ids.extend(reminders.iter().map(|reminder| reminder.id.clone()));
            total += reminders.len();
            spent += weights[index];
            self.emit("sync_progress",json!({"stage":"reminders","list":list.title,"index":index+1,"done":index+1,"of":work.len(),"total":total,"percent":((1000*spent/budget) as f64)/10.0}));
        }
        let tags = self
            .client
            .lock()
            .await
            .tags_for_reminders(&reminder_ids)
            .await?;
        for id in &reminder_ids {
            self.cache
                .replace_tags_for(id, tags.get(id).map(Vec::as_slice).unwrap_or(&[]))?;
        }
        if let Some(value) = cursor.as_deref() {
            self.cache.set_meta(CURSOR_KEY, Some(value))?;
        }
        let now = to_iso(Utc::now());
        self.cache.set_meta(LAST_FULL_KEY, Some(&now))?;
        self.cache.set_meta(LAST_SYNC_KEY, Some(&now))?;
        self.cache
            .set_meta(READ_PROTOCOL_KEY, Some(READ_PROTOCOL_VERSION))?;
        self.emit("sync_finished", json!({"mode":"full","reminders":total}));
        Ok(json!({"mode":"full","lists":lists.len(),"reminders":total}))
    }

    async fn delta_sync(&self) -> Result<Value> {
        let cursor = self
            .cache
            .get_meta(CURSOR_KEY)?
            .ok_or_else(|| AppError::internal("Delta sync started without a cursor", ""))?;
        self.emit("sync_started", json!({"mode":"delta","determinate":false}));
        let lists = self.client.lock().await.lists().await?;
        self.cache.replace_lists(&lists)?;
        self.emit(
            "sync_progress",
            json!({"stage":"lists","count":lists.len()}),
        );
        let (updated, deleted, new_cursor) = self
            .client
            .lock()
            .await
            .changes_since(Some(&cursor))
            .await?;
        self.emit(
            "sync_progress",
            json!({"stage":"changes","total":updated.len()+deleted.len()}),
        );
        self.cache.upsert_reminders(&updated)?;
        if !updated.is_empty() {
            let ids = updated
                .iter()
                .map(|reminder| reminder.id.clone())
                .collect::<Vec<_>>();
            let tags = self.client.lock().await.tags_for_reminders(&ids).await?;
            for reminder in &updated {
                self.cache.replace_tags_for(
                    &reminder.id,
                    tags.get(&reminder.id).map(Vec::as_slice).unwrap_or(&[]),
                )?;
            }
        }
        for id in &deleted {
            let mut fields = serde_json::Map::new();
            fields.insert("deleted".into(), json!(1));
            self.cache.apply_local_edit(id, &fields)?;
            self.cache.clear_dirty(id, None)?;
        }
        if let Some(value) = new_cursor.as_deref() {
            self.cache.set_meta(CURSOR_KEY, Some(value))?;
        }
        self.cache
            .set_meta(LAST_SYNC_KEY, Some(&to_iso(Utc::now())))?;
        self.emit(
            "sync_finished",
            json!({"mode":"delta","updated":updated.len(),"deleted":deleted.len()}),
        );
        Ok(json!({"mode":"delta","updated":updated.len(),"deleted":deleted.len()}))
    }

    async fn flush_outbox(&self) -> Result<Value> {
        let Ok(_push_guard) = self.pushing.try_lock() else {
            return Ok(json!({"pushed":0,"conflicts":0,"failed":0,"skipped":true}));
        };
        let mut pushed = 0;
        let mut conflicts = 0;
        let mut failed = 0;
        for item in self.cache.pending(100)? {
            let result = match item.op.as_str() {
                "create" => self.client.lock().await.create(&item.payload).await,
                "update" => {
                    self.client
                        .lock()
                        .await
                        .update(&item.reminder_id, &item.payload, item.base_tag.as_deref())
                        .await
                }
                "delete" => {
                    self.client
                        .lock()
                        .await
                        .delete(&item.reminder_id, item.base_tag.as_deref())
                        .await
                }
                _ => {
                    self.cache.dequeue(item.seq)?;
                    continue;
                }
            };
            match result {
                Ok(remote) => {
                    if item.op == "create" {
                        self.cache.replace_id(&item.reminder_id, &remote.id)?;
                    }
                    self.cache
                        .clear_dirty(&remote.id, remote.change_tag.as_deref())?;
                    self.cache.upsert_reminders(&[remote])?;
                    self.cache.dequeue(item.seq)?;
                    pushed += 1;
                }
                Err(AppError::Conflict { detail, .. }) => {
                    let local = serde_json::to_value(
                        self.cache.reminder(&item.reminder_id)?.unwrap_or_default(),
                    )?;
                    let remote: Value =
                        serde_json::from_str(&detail).unwrap_or_else(|_| json!({"raw":detail}));
                    self.cache
                        .record_conflict(&item.reminder_id, &local, &remote)?;
                    if let Ok(reminder) = serde_json::from_value::<Reminder>(remote.clone()) {
                        self.cache
                            .clear_dirty(&item.reminder_id, reminder.change_tag.as_deref())?;
                        self.cache.upsert_reminders(&[reminder])?;
                    }
                    self.cache.dequeue(item.seq)?;
                    conflicts += 1;
                    self.emit("conflict", json!({"reminder_id":item.reminder_id}));
                }
                Err(error) => {
                    self.cache.record_failure(item.seq, &error.to_string())?;
                    failed += 1;
                    self.emit(
                        "push_failed",
                        json!({"reminder_id":item.reminder_id,"error":error.body()}),
                    );
                    if matches!(
                        error,
                        AppError::AuthRequired { .. }
                            | AppError::TwoFactorRequired { .. }
                            | AppError::TermsRequired { .. }
                    ) {
                        return Err(error);
                    }
                }
            }
        }
        Ok(json!({"pushed":pushed,"conflicts":conflicts,"failed":failed}))
    }
}

use std::path::Path;
use std::sync::{Arc, Mutex as StdMutex};

use chrono::Utc;
use serde_json::{Map, Value, json};
use tokio::sync::{Mutex, mpsc::UnboundedSender};
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::cache::{Cache, ReminderQuery};
use crate::error::{AppError, Result};
use crate::icloud::ICloudClient;
use crate::model::Reminder;
use crate::notifications;
use crate::sync::{CURSOR_KEY, LAST_SYNC_KEY, SyncEngine};
use crate::timeutil::normalize_due;

const MAX_APPLE_ID_BYTES: usize = 320;
const MAX_PASSWORD_BYTES: usize = 4096;
const MAX_TITLE_BYTES: usize = 4096;
const MAX_DESCRIPTION_BYTES: usize = 100_000;
const MAX_ID_BYTES: usize = 2048;
const MAX_SEARCH_BYTES: usize = 4096;

/// How long to wait between restore attempts that failed for reasons that have
/// nothing to do with the session. A laptop resuming from sleep, or one that
/// launched before its Wi-Fi came up, is back within a couple of minutes; the
/// last step keeps a longer outage from turning into a password prompt the
/// moment the user looks at the window.
const RESTORE_BACKOFF_SECONDS: [u64; 5] = [5, 15, 45, 120, 300];

pub struct Server {
    pub cache: Arc<Cache>,
    client: Arc<Mutex<ICloudClient>>,
    sync: Arc<SyncEngine>,
    events: UnboundedSender<Value>,
    auth_snapshot: StdMutex<Value>,
}

impl Server {
    pub fn open(
        data_dir: &Path,
        apple_id: Option<String>,
        events: UnboundedSender<Value>,
    ) -> Result<Arc<Self>> {
        let cache = Arc::new(Cache::open(&data_dir.join("cache.db"))?);
        let account = apple_id
            .or(cache.get_meta("apple_id")?)
            .map(|value| value.trim().to_lowercase())
            .unwrap_or_default();
        let client_value = ICloudClient::new(account)?;
        let auth_snapshot = StdMutex::new(client_value.status());
        let client = Arc::new(Mutex::new(client_value));
        let sync = Arc::new(SyncEngine::new(
            cache.clone(),
            client.clone(),
            events.clone(),
        ));
        Ok(Arc::new(Self {
            cache,
            client,
            sync,
            events,
            auth_snapshot,
        }))
    }

    pub async fn start(self: &Arc<Self>) {
        let apple_id = self.client.lock().await.apple_id().to_owned();
        if apple_id.is_empty() {
            self.emit("ready", json!({"apple_id":apple_id}));
            return;
        }
        {
            let mut client = self.client.lock().await;
            client.restoring = true;
            self.set_auth_snapshot(client.status());
        }
        self.emit("ready", json!({"apple_id":apple_id}));
        let server = self.clone();
        tokio::spawn(async move {
            // A restore that failed because Apple was unreachable is retried
            // rather than reported. Launching before the network is up is the
            // most common way this fails, and answering it with the sign-in form
            // is what "it signs me out all the time" looked like from outside.
            let mut ok = {
                let mut client = server.client.lock().await;
                client.restore().await
            };
            for delay in RESTORE_BACKOFF_SECONDS {
                if ok {
                    break;
                }
                {
                    let mut client = server.client.lock().await;
                    if !client.restore_is_retryable() {
                        break;
                    }
                    // Keep saying "restoring" across the wait, so the gate holds
                    // its "signing you back in" state rather than flashing a
                    // password form over a blip it is about to recover from.
                    client.restoring = true;
                    server.set_auth_snapshot(client.status());
                }
                tokio::time::sleep(std::time::Duration::from_secs(delay)).await;
                let mut client = server.client.lock().await;
                ok = client.restore().await;
            }
            let status = server.client.lock().await.status();
            server.set_auth_snapshot(status.clone());
            server.emit("auth_changed", status);
            if ok {
                server.kick_sync(server.cache.lists().map(|v| v.is_empty()).unwrap_or(true));
            }
        });
    }

    fn emit(&self, event: &str, data: Value) {
        let _ = self.events.send(json!({"event":event,"data":data}));
    }

    fn set_auth_snapshot(&self, status: Value) {
        if let Ok(mut snapshot) = self.auth_snapshot.lock() {
            *snapshot = status;
        }
    }

    pub async fn dispatch(self: &Arc<Self>, method: &str, params: Value) -> Result<Value> {
        let object = params.as_object().cloned().unwrap_or_default();
        match method {
            "ping" => Ok(json!({"pong":true})),
            "auth_status" => self.auth_status().await,
            "login" => self.login(&object).await,
            "request_2fa" => {
                // Report the whole outcome, not just a bool: the delivery route
                // decides whether the screen should say "your iPhone" or "your
                // texts", and a user cannot type a code they are looking past.
                let mut client = self.client.lock().await;
                // "sms" is the user choosing a text, never the app deciding for
                // them: a text they did not ask for retires the code already on
                // their phone.
                let sms = object.get("method").and_then(Value::as_str) == Some("sms");
                let sent = client.request_2fa(sms).await?;
                self.set_auth_snapshot(client.status());
                Ok(sent)
            }
            "submit_2fa" => {
                let code = required_string(&object, "code")?;
                let mut client = self.client.lock().await;
                let result = client.submit_2fa(code).await;
                self.set_auth_snapshot(client.status());
                let status = result?;
                drop(client);
                self.kick_sync(self.cache.lists()?.is_empty());
                Ok(status)
            }
            "lists" => Ok(Value::Array(self.cache.lists()?)),
            "reminders" => {
                let search = optional_string(&object, "search", MAX_SEARCH_BYTES)?;
                Ok(serde_json::to_value(
                    self.cache.reminders(ReminderQuery {
                        list_id: object.get("list_id").and_then(Value::as_str),
                        tag: object.get("tag").and_then(Value::as_str),
                        scope: object.get("scope").and_then(Value::as_str),
                        include_completed: object
                            .get("include_completed")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                        search,
                        sort: object.get("sort").and_then(Value::as_str),
                        limit: object.get("limit").and_then(Value::as_i64).unwrap_or(1000),
                    })?,
                )?)
            }
            "reminder" => Ok(serde_json::to_value(
                self.cache.reminder(required_string(&object, "id")?)?,
            )?),
            "tags" => Ok(Value::Array(self.cache.all_tags()?)),
            "smart_counts" => self.cache.smart_counts(),
            "settings" => self.cache.settings(),
            "set_settings" => self.cache.set_settings(&object),
            "sign_out" => {
                let mut client = self.client.lock().await;
                client.sign_out()?;
                self.set_auth_snapshot(client.status());
                drop(client);
                if object.get("purge").and_then(Value::as_bool) == Some(true) {
                    self.cache.set_meta(CURSOR_KEY, None)?;
                }
                Ok(json!({"signed_out":true}))
            }
            "create_reminder" => self.create_reminder(&object),
            "update_reminder" => self.update_reminder(&object),
            "delete_reminder" => self.delete_reminder(&object),
            "restore_reminder" => self.restore_reminder(&object),
            "sync" => {
                let full = object.get("full").and_then(Value::as_bool).unwrap_or(false);
                if self.sync.running() {
                    Ok(json!({"queued":false,"reason":"already running"}))
                } else {
                    self.kick_sync(full);
                    Ok(json!({"queued":true}))
                }
            }
            "sync_status" => self.sync_status(),
            "due_notifications" => self.due_notifications(&object),
            "conflicts" => Ok(serde_json::to_value(self.cache.conflicts()?)?),
            "resolve_conflict" => self.resolve_conflict(&object),
            "shutdown" => Ok(json!({"bye":true,"shutdown":true})),
            _ => Err(AppError::BadRequest {
                message: format!("unknown method {method:?}"),
                detail: "NO_METHOD".into(),
            }),
        }
    }

    async fn auth_status(&self) -> Result<Value> {
        let mut status = if let Ok(client) = self.client.try_lock() {
            let status = client.status();
            self.set_auth_snapshot(status.clone());
            status
        } else {
            self.auth_snapshot
                .lock()
                .map(|value| value.clone())
                .unwrap_or_else(|_| json!({"authenticated":false,"restoring":true}))
        };
        status["has_cache"] = json!(!self.cache.lists()?.is_empty());
        status["last_sync"] = serde_json::to_value(self.cache.get_meta(LAST_SYNC_KEY)?)?;
        Ok(status)
    }

    async fn login(self: &Arc<Self>, p: &Map<String, Value>) -> Result<Value> {
        let apple_id = p
            .get("apple_id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .or_else(|| self.cache.get_meta("apple_id").ok().flatten())
            .ok_or_else(|| AppError::AuthRequired {
                message: "No Apple ID provided".into(),
                detail: String::new(),
            })?
            .trim()
            .to_lowercase();
        validate_length("apple_id", &apple_id, MAX_APPLE_ID_BYTES)?;
        self.cache.set_meta("apple_id", Some(&apple_id))?;
        let remember = self
            .cache
            .settings()?
            .get("remember_password")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let password = if let Some(password) = p.get("password").and_then(Value::as_str) {
            validate_length("password", password, MAX_PASSWORD_BYTES)?;
            Some(Zeroizing::new(password.to_owned()))
        } else {
            None
        };
        let mut client = self.client.lock().await;
        client.set_apple_id(apple_id)?;
        let result = client
            .connect(
                password,
                remember,
                p.get("accept_terms")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            )
            .await;
        self.set_auth_snapshot(client.status());
        let status = result?;
        drop(client);
        self.kick_sync(self.cache.lists()?.is_empty());
        Ok(status)
    }

    fn create_reminder(&self, p: &Map<String, Value>) -> Result<Value> {
        let list_id = required_string(p, "list_id")?;
        let title = optional_string(p, "title", MAX_TITLE_BYTES)?.unwrap_or("");
        let description = optional_string(p, "description", MAX_DESCRIPTION_BYTES)?.unwrap_or("");
        let all_day = p.get("all_day").and_then(Value::as_bool).unwrap_or(false);
        let reminder = Reminder {
            id: format!("local/{}", Uuid::new_v4()),
            list_id: list_id.into(),
            title: title.into(),
            description: description.into(),
            due_date: p
                .get("due_date")
                .map(|v| normalize_due(v, all_day))
                .transpose()?
                .flatten(),
            priority: p.get("priority").and_then(Value::as_i64).unwrap_or(0),
            completed: false,
            flagged: p.get("flagged").and_then(Value::as_bool).unwrap_or(false),
            all_day,
            ..Reminder::default()
        };
        self.cache.insert_local_reminder(&reminder)?;
        self.cache.enqueue(
            &reminder.id,
            "create",
            &serde_json::to_value(&reminder)?,
            None,
        )?;
        self.kick_push();
        Ok(serde_json::to_value(
            self.cache.reminder(&reminder.id)?.unwrap_or(reminder),
        )?)
    }

    fn update_reminder(&self, p: &Map<String, Value>) -> Result<Value> {
        let id = required_string(p, "id")?;
        let current = self
            .cache
            .reminder(id)?
            .ok_or_else(|| AppError::bad_request(format!("No such reminder: {id}")))?;
        let mut fields = Map::new();
        if let Some(value) = optional_string(p, "title", MAX_TITLE_BYTES)? {
            fields.insert("title".into(), Value::String(value.to_owned()));
        }
        if let Some(value) = optional_string(p, "description", MAX_DESCRIPTION_BYTES)? {
            fields.insert("description".into(), Value::String(value.to_owned()));
        }
        for key in ["priority", "completed", "flagged"] {
            if let Some(value) = p.get(key) {
                fields.insert(key.into(), value.clone());
            }
        }
        if let Some(value) = p.get("all_day") {
            fields.insert(
                "all_day".into(),
                json!(value.as_bool().unwrap_or(false) as i64),
            );
        }
        let all_day = fields
            .get("all_day")
            .and_then(Value::as_i64)
            .map(|v| v != 0)
            .unwrap_or(current.all_day);
        if let Some(value) = p.get("due_date") {
            fields.insert(
                "due_date".into(),
                normalize_due(value, all_day)?
                    .map(Value::String)
                    .unwrap_or(Value::Null),
            );
        } else if p.contains_key("all_day") && all_day
            && let Some(value) = current.due_date.as_ref() {
                fields.insert(
                    "due_date".into(),
                    normalize_due(&Value::String(value.clone()), true)?
                        .map(Value::String)
                        .unwrap_or(Value::Null),
                );
            }
        for key in ["completed", "flagged"] {
            if let Some(value) = fields.get_mut(key)
                && let Some(flag) = value.as_bool() {
                    *value = json!(flag as i64);
                }
        }
        self.cache.apply_local_edit(id, &fields)?;
        self.cache.enqueue(
            id,
            "update",
            &Value::Object(fields),
            current.change_tag.as_deref(),
        )?;
        self.kick_push();
        Ok(serde_json::to_value(self.cache.reminder(id)?)?)
    }

    fn delete_reminder(&self, p: &Map<String, Value>) -> Result<Value> {
        let id = required_string(p, "id")?;
        let current = self
            .cache
            .reminder(id)?
            .ok_or_else(|| AppError::bad_request(format!("No such reminder: {id}")))?;
        let mut fields = Map::new();
        fields.insert("deleted".into(), json!(1));
        self.cache.apply_local_edit(id, &fields)?;
        self.cache
            .enqueue(id, "delete", &json!({}), current.change_tag.as_deref())?;
        self.kick_push();
        Ok(json!({"deleted":id}))
    }

    fn restore_reminder(&self, p: &Map<String, Value>) -> Result<Value> {
        let id = required_string(p, "id")?;
        let current = self
            .cache
            .reminder(id)?
            .ok_or_else(|| AppError::bad_request(format!("No such reminder: {id}")))?;
        let mut fields = Map::new();
        fields.insert("deleted".into(), json!(0));
        self.cache.apply_local_edit(id, &fields)?;
        self.cache.enqueue(
            id,
            "update",
            &json!({"deleted":false}),
            current.change_tag.as_deref(),
        )?;
        self.kick_push();
        Ok(serde_json::to_value(self.cache.reminder(id)?)?)
    }

    fn sync_status(&self) -> Result<Value> {
        let settings = self.cache.settings()?;
        Ok(
            json!({"running":self.sync.running(),"last_sync":self.cache.get_meta(LAST_SYNC_KEY)?,"has_cursor":self.cache.get_meta(CURSOR_KEY)?.is_some(),"pending_pushes":self.cache.pending(100)?.len(),"conflicts":self.cache.conflicts()?.len(),"sync_minutes":settings.get("sync_minutes").and_then(Value::as_i64).unwrap_or(10)}),
        )
    }
    fn due_notifications(&self, p: &Map<String, Value>) -> Result<Value> {
        let settings = self.cache.settings()?;
        if settings
            .get("notifications_enabled")
            .and_then(Value::as_bool)
            == Some(false)
        {
            return Ok(json!({"toasts":[],"notified_ids":[]}));
        }
        let now = p
            .get("now")
            .and_then(Value::as_str)
            .map(crate::timeutil::parse_instant)
            .transpose()?
            .unwrap_or_else(Utc::now);
        Ok(serde_json::to_value(notifications::plan(
            &self.cache,
            now,
            p.get("stale_after_minutes")
                .and_then(Value::as_i64)
                .or_else(|| settings.get("stale_after_minutes").and_then(Value::as_i64))
                .unwrap_or(60),
            p.get("max_individual")
                .and_then(Value::as_u64)
                .or_else(|| {
                    settings
                        .get("max_individual_toasts")
                        .and_then(Value::as_u64)
                })
                .unwrap_or(3) as usize,
        )?)?)
    }
    fn resolve_conflict(&self, p: &Map<String, Value>) -> Result<Value> {
        let id = p
            .get("id")
            .and_then(Value::as_i64)
            .ok_or_else(|| AppError::bad_request("missing conflict id"))?;
        if p.get("keep").and_then(Value::as_str) == Some("local")
            && let Some(conflict) = self.cache.conflicts()?.into_iter().find(|c| c.id == id)
                && let Some(local) = conflict.local.as_object() {
                    let fields: Map<_, _> = local
                        .iter()
                        .filter(|(k, _)| {
                            matches!(
                                k.as_str(),
                                "title" | "description" | "due_date" | "priority" | "completed"
                            )
                        })
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect();
                    self.cache
                        .apply_local_edit(&conflict.reminder_id, &fields)?;
                    let current = self
                        .cache
                        .reminder(&conflict.reminder_id)?
                        .unwrap_or_default();
                    self.cache.enqueue(
                        &conflict.reminder_id,
                        "update",
                        &Value::Object(fields),
                        current.change_tag.as_deref(),
                    )?;
                    self.kick_push();
                }
        self.cache.resolve_conflict(id)?;
        Ok(json!({"resolved":id}))
    }
    fn kick_sync(self: &Arc<Self>, full: bool) {
        let sync = self.sync.clone();
        let events = self.events.clone();
        tokio::spawn(async move {
            if let Err(error) = sync.sync_now(full).await {
                let _ = events.send(json!({"event":"sync_error","data":error.body()}));
            }
        });
    }
    fn kick_push(&self) {
        let sync = self.sync.clone();
        let events = self.events.clone();
        tokio::spawn(async move {
            if let Err(error) = sync.push_now().await {
                let _ = events.send(json!({"event":"sync_error","data":error.body()}));
            }
        });
    }
}

fn required_string<'a>(object: &'a Map<String, Value>, name: &str) -> Result<&'a str> {
    let value = object
        .get(name)
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| AppError::bad_request(format!("missing {name}")))?;
    validate_length(name, value, MAX_ID_BYTES)?;
    Ok(value)
}

fn optional_string<'a>(
    object: &'a Map<String, Value>,
    name: &str,
    max_bytes: usize,
) -> Result<Option<&'a str>> {
    let Some(value) = object.get(name) else {
        return Ok(None);
    };
    let value = value
        .as_str()
        .ok_or_else(|| AppError::bad_request(format!("{name} must be text")))?;
    validate_length(name, value, max_bytes)?;
    Ok(Some(value))
}

fn validate_length(name: &str, value: &str, max_bytes: usize) -> Result<()> {
    if value.len() > max_bytes {
        return Err(AppError::bad_request(format!(
            "{name} is longer than the supported {max_bytes} bytes"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::Server;
    use serde_json::{Value, json};
    use std::sync::Arc;
    use tokio::sync::mpsc::{self, UnboundedReceiver};

    fn server() -> (tempfile::TempDir, Arc<Server>, UnboundedReceiver<Value>) {
        let dir = tempfile::tempdir().expect("temp dir");
        let (tx, rx) = mpsc::unbounded_channel();
        let server = Server::open(dir.path(), Some("someone@example.com".into()), tx)
            .expect("open server");
        (dir, server, rx)
    }

    #[tokio::test]
    async fn ping_answers_without_an_account() {
        let (_dir, server, _rx) = server();
        let result = server.dispatch("ping", json!({})).await.expect("ping works");
        assert_eq!(result, json!({"pong": true}));
    }

    #[tokio::test]
    async fn an_unknown_method_reports_no_method() {
        let (_dir, server, _rx) = server();
        let error = server
            .dispatch("definitely_not_a_method", json!({}))
            .await
            .expect_err("unknown methods must fail");
        assert_eq!(error.code(), "BAD_REQUEST");
        // main.rs rewrites this marker into the NO_METHOD wire code.
        assert_eq!(error.body().detail, "NO_METHOD");
    }

    #[tokio::test]
    async fn missing_parameters_are_rejected_rather_than_defaulted() {
        let (_dir, server, _rx) = server();
        for (method, params) in [
            ("reminder", json!({})),
            ("submit_2fa", json!({})),
            ("create_reminder", json!({"title": "no list"})),
            ("update_reminder", json!({"title": "no id"})),
        ] {
            let error = server
                .dispatch(method, params)
                .await
                .expect_err(&format!("{method} must require its parameters"));
            assert_eq!(error.code(), "BAD_REQUEST", "{method}");
        }
    }

    #[tokio::test]
    async fn oversized_strings_are_refused() {
        let (_dir, server, _rx) = server();
        let error = server
            .dispatch(
                "create_reminder",
                json!({"list_id": "list-1", "title": "t".repeat(8192)}),
            )
            .await
            .expect_err("an over-long title must be refused");
        assert_eq!(error.code(), "BAD_REQUEST");
    }

    #[tokio::test]
    async fn a_reminder_survives_create_update_and_delete() {
        let (_dir, server, _rx) = server();
        let created = server
            .dispatch(
                "create_reminder",
                json!({"list_id": "list-1", "title": "Water the plants"}),
            )
            .await
            .expect("create works offline");
        let id = created["id"].as_str().expect("an id").to_owned();
        assert!(id.starts_with("local/"), "unsynced rows get a local id");

        server
            .dispatch("update_reminder", json!({"id": id, "title": "Water them well"}))
            .await
            .expect("update works offline");
        let fetched = server
            .dispatch("reminder", json!({"id": id}))
            .await
            .expect("read back");
        assert_eq!(fetched["title"], json!("Water them well"));

        server
            .dispatch("delete_reminder", json!({"id": id}))
            .await
            .expect("delete works offline");
        let listed = server.dispatch("reminders", json!({})).await.expect("list");
        assert!(
            listed.as_array().is_some_and(|rows| rows.is_empty()),
            "a deleted reminder should leave the default view"
        );
    }

    #[tokio::test]
    async fn settings_round_trip_through_the_protocol() {
        let (_dir, server, _rx) = server();
        server
            .dispatch("set_settings", json!({"sync_minutes": 45}))
            .await
            .expect("settings accepted");
        let settings = server.dispatch("settings", json!({})).await.expect("read back");
        assert_eq!(settings["sync_minutes"], json!(45));
    }

    #[tokio::test]
    async fn signing_out_with_purge_leaves_the_cursor_readable() {
        let (_dir, server, _rx) = server();
        server
            .dispatch("sign_out", json!({"purge": true}))
            .await
            .expect("sign out works");
        // Regression: clearing the cursor used to store SQL NULL, after which
        // every later read failed and sync could never restart.
        server
            .dispatch("sync_status", json!({}))
            .await
            .expect("sync status must still be readable after a purge");
    }

    #[tokio::test]
    async fn auth_status_reports_a_signed_out_account() {
        let (_dir, server, _rx) = server();
        let status = server.dispatch("auth_status", json!({})).await.expect("status");
        assert_eq!(status["authenticated"], json!(false));
        assert_eq!(status["has_cache"], json!(false));
    }
}

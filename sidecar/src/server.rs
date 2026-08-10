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
            let ok = {
                let mut client = server.client.lock().await;
                client.restore().await
            };
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
            "request_2fa" => Ok(json!({"sent":self.client.lock().await.request_2fa().await?})),
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
        } else if p.contains_key("all_day") && all_day {
            if let Some(value) = current.due_date.as_ref() {
                fields.insert(
                    "due_date".into(),
                    normalize_due(&Value::String(value.clone()), true)?
                        .map(Value::String)
                        .unwrap_or(Value::Null),
                );
            }
        }
        for key in ["completed", "flagged"] {
            if let Some(value) = fields.get_mut(key) {
                if let Some(flag) = value.as_bool() {
                    *value = json!(flag as i64);
                }
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
        if p.get("keep").and_then(Value::as_str) == Some("local") {
            if let Some(conflict) = self.cache.conflicts()?.into_iter().find(|c| c.id == id) {
                if let Some(local) = conflict.local.as_object() {
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
            }
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

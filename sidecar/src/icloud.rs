use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde_json::{Map, Value, json};
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::auth::{AuthClient, SessionState};
use crate::cloudkit::{self, CloudKit};
use crate::error::{AppError, Result};
use crate::model::{Reminder, ReminderList, Tag};
use crate::secrets;
use crate::timeutil::{
    floating_millis_to_instant, instant_to_floating_millis, parse_instant, to_iso,
};

pub struct ICloudClient {
    apple_id: String,
    auth: AuthClient,
    connected: bool,
    pending_2fa: bool,
    pub restoring: bool,
}

impl ICloudClient {
    pub fn new(apple_id: String) -> Result<Self> {
        let state = secrets::session(&apple_id)?
            .and_then(|raw| serde_json::from_str::<SessionState>(&raw).ok());
        Ok(Self {
            apple_id,
            auth: AuthClient::new(state)?,
            connected: false,
            pending_2fa: false,
            restoring: false,
        })
    }

    pub fn apple_id(&self) -> &str {
        &self.apple_id
    }
    pub fn connected(&self) -> bool {
        self.connected && !self.pending_2fa
    }
    pub fn set_apple_id(&mut self, value: String) -> Result<()> {
        if value == self.apple_id {
            return Ok(());
        }
        self.apple_id = value;
        self.auth = AuthClient::new(None)?;
        self.connected = false;
        self.pending_2fa = false;
        Ok(())
    }

    pub fn status(&self) -> Value {
        json!({
            "authenticated": self.connected(), "apple_id": self.apple_id,
            "needs_2fa": self.pending_2fa, "trusted_session":self.auth.state.trusted_session,
            "restoring":self.restoring, "can_restore":secrets::has_password(&self.apple_id)
        })
    }

    pub async fn connect(
        &mut self,
        password: Option<Zeroizing<String>>,
        remember: bool,
        accept_terms: bool,
    ) -> Result<Value> {
        let password = match password {
            Some(value) => value,
            None => secrets::password(&self.apple_id)?.ok_or_else(|| AppError::AuthRequired {
                message: "No saved iCloud credential is available".into(),
                detail: String::new(),
            })?,
        };
        match self
            .auth
            .sign_in(&self.apple_id, password.as_str(), accept_terms)
            .await
        {
            Ok(_) => {
                self.pending_2fa =
                    self.auth.state.hsa_version >= 2 && !self.auth.state.trusted_session;
                self.connected = !self.pending_2fa;
                if remember {
                    secrets::set_password(&self.apple_id, &password)?;
                }
                self.persist_session()?;
                if self.pending_2fa {
                    return Err(AppError::TwoFactorRequired {
                        message: "Two-factor authentication required".into(),
                        detail: String::new(),
                    });
                }
                Ok(self.status())
            }
            Err(error @ AppError::TwoFactorRequired { .. }) => {
                self.pending_2fa = true;
                if remember {
                    secrets::set_password(&self.apple_id, &password)?;
                }
                self.persist_session()?;
                Err(error)
            }
            Err(error) => Err(error),
        }
    }

    pub async fn restore(&mut self) -> bool {
        if self.connected() {
            return true;
        }
        self.restoring = true;
        let result = self.connect(None, true, false).await.is_ok();
        self.restoring = false;
        result
    }

    pub fn invalidate(&mut self) {
        self.connected = false;
        self.pending_2fa = false;
    }
    pub async fn request_2fa(&self) -> Result<bool> {
        self.auth.request_2fa().await
    }
    pub async fn submit_2fa(&mut self, code: &str) -> Result<Value> {
        self.auth.submit_2fa(code).await?;
        self.pending_2fa = false;
        self.connected = true;
        self.persist_session()?;
        Ok(self.status())
    }
    pub fn sign_out(&mut self) -> Result<()> {
        secrets::delete_password(&self.apple_id)?;
        secrets::delete_session(&self.apple_id)?;
        self.invalidate();
        Ok(())
    }
    fn persist_session(&self) -> Result<()> {
        let session = Zeroizing::new(serde_json::to_string(&self.auth.state)?);
        secrets::set_session(&self.apple_id, &session)
    }
    fn cloudkit(&self) -> Result<CloudKit<'_>> {
        if !self.connected() {
            return Err(AppError::AuthRequired {
                message: "Not signed in to iCloud".into(),
                detail: String::new(),
            });
        }
        CloudKit::new(&self.auth)
    }

    pub async fn lists(&self) -> Result<Vec<ReminderList>> {
        // Reminders lists live in a custom CloudKit zone. Apple's web client
        // exposes the full list snapshot through /changes/zone; querying the
        // raw List record type can return a successful but empty response.
        let (records, _) = self
            .cloudkit()?
            .changes(None, Some(&["List"]))
            .await?;
        let mut lists = Vec::new();
        for (position, record) in records.iter().enumerate() {
            if cloudkit::int_field(record, "Deleted") != 0 {
                continue;
            }
            let id = record
                .get("recordName")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            if id.is_empty() {
                continue;
            }
            lists.push(ReminderList {
                id,
                title: cloudkit::text_field(record, "Name").unwrap_or_else(|| "Untitled".into()),
                color_hex: parse_color(cloudkit::text_field(record, "Color").as_deref()),
                count: cloudkit::int_field(record, "Count"),
                is_group: cloudkit::int_field(record, "IsGroup") != 0,
                position: position as i64,
            });
        }
        Ok(lists)
    }

    pub async fn reminders_for(&self, list_id: &str) -> Result<Vec<Reminder>> {
        // `reminderList` is Apple's compound query. It returns Reminder rows
        // together with their related records. A direct Reminder query is
        // accepted by CloudKit but currently returns no rows for this zone.
        let filters = vec![
            json!({"fieldName":"List","comparator":"EQUALS","fieldValue":cloudkit::reference(list_id)}),
            json!({"fieldName":"includeCompleted","comparator":"EQUALS","fieldValue":cloudkit::int(1)}),
            json!({"fieldName":"LookupValidatingReference","comparator":"EQUALS","fieldValue":cloudkit::int(1)}),
        ];
        let records = self.cloudkit()?.query("reminderList", filters).await?;
        Ok(records
            .iter()
            .filter(|record| record.get("recordType").and_then(Value::as_str) == Some("Reminder"))
            .filter_map(record_to_reminder)
            .collect())
    }

    pub async fn tags_for_reminders(&self, ids: &[String]) -> Result<BTreeMap<String, Vec<Tag>>> {
        let reminder_ids: std::collections::BTreeSet<_> = ids.iter().map(String::as_str).collect();
        let hashtags = self
            .cloudkit()?
            .query("Hashtag", Vec::new())
            .await
            .unwrap_or_default();
        let mut tags: BTreeMap<String, Vec<Tag>> = BTreeMap::new();
        for record in hashtags {
            if cloudkit::int_field(&record, "Deleted") != 0 {
                continue;
            }
            let Some(reminder_id) = cloudkit::reference_field(&record, "Reminder") else {
                continue;
            };
            if !reminder_ids.contains(reminder_id.as_str()) {
                continue;
            }
            let Some(name) = cloudkit::text_field(&record, "Name") else {
                continue;
            };
            let id = record
                .get("recordName")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            tags.entry(reminder_id.clone()).or_default().push(Tag {
                id,
                name,
                reminder_id,
            });
        }
        Ok(tags)
    }

    pub async fn changes_since(
        &self,
        cursor: Option<&str>,
    ) -> Result<(Vec<Reminder>, Vec<String>, Option<String>)> {
        let (records, new_cursor) = self
            .cloudkit()?
            .changes(cursor, Some(&["Reminder"]))
            .await?;
        let mut updated = Vec::new();
        let mut deleted = Vec::new();
        for record in records {
            if record.get("recordType").and_then(Value::as_str) != Some("Reminder") {
                continue;
            }
            let id = record
                .get("recordName")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            if record.get("deleted").and_then(Value::as_bool) == Some(true)
                || record.get("reason").and_then(Value::as_str) == Some("deleted")
            {
                if !id.is_empty() {
                    deleted.push(id);
                }
            } else if let Some(reminder) = record_to_reminder(&record) {
                updated.push(reminder);
            }
        }
        Ok((updated, deleted, new_cursor))
    }

    pub async fn sync_cursor(&self) -> Result<Option<String>> {
        let (_, _, cursor) = self.changes_since(None).await?;
        Ok(cursor)
    }

    pub async fn create(&self, payload: &Value) -> Result<Reminder> {
        let object = payload
            .as_object()
            .ok_or_else(|| AppError::bad_request("create payload must be an object"))?;
        let list_id = required_string(object, "list_id")?;
        let record_name = format!(
            "Reminder/{}",
            Uuid::new_v4().hyphenated().to_string().to_uppercase()
        );
        let now = Utc::now().timestamp_millis();
        let mut values = Map::new();
        values.insert("List".into(), cloudkit::reference(list_id));
        values.insert(
            "Title".into(),
            cloudkit::encrypted_bytes(object.get("title").and_then(Value::as_str).unwrap_or("")),
        );
        values.insert(
            "Description".into(),
            cloudkit::encrypted_bytes(
                object
                    .get("description")
                    .and_then(Value::as_str)
                    .unwrap_or(""),
            ),
        );
        values.insert(
            "Priority".into(),
            cloudkit::int(object.get("priority").and_then(Value::as_i64).unwrap_or(0)),
        );
        values.insert(
            "Flagged".into(),
            cloudkit::int(
                object
                    .get("flagged")
                    .and_then(Value::as_bool)
                    .unwrap_or(false) as i64,
            ),
        );
        values.insert(
            "AllDay".into(),
            cloudkit::int(
                object
                    .get("all_day")
                    .and_then(Value::as_bool)
                    .unwrap_or(false) as i64,
            ),
        );
        values.insert("Completed".into(), cloudkit::int(0));
        values.insert("Deleted".into(), cloudkit::int(0));
        values.insert("CreationDate".into(), cloudkit::timestamp(now));
        values.insert("LastModifiedDate".into(), cloudkit::timestamp(now));
        if let Some(raw) = object.get("due_date").and_then(Value::as_str) {
            values.insert(
                "DueDate".into(),
                cloudkit::timestamp(instant_to_floating_millis(parse_instant(raw)?, None)),
            );
        }
        values.insert(
            "ResolutionTokenMap".into(),
            cloudkit::resolution_tokens(&[
                "title",
                "description",
                "priority",
                "flagged",
                "allDay",
                "dueDate",
            ]),
        );
        let result = self
            .cloudkit()?
            .modify(vec![cloudkit::operation(
                "create",
                &record_name,
                "Reminder",
                None,
                Value::Object(values),
            )])
            .await?;
        result.first().and_then(record_to_reminder).ok_or_else(|| {
            AppError::internal(
                "iCloud did not return the created reminder",
                result.first().map(Value::to_string).unwrap_or_default(),
            )
        })
    }

    pub async fn update(
        &self,
        id: &str,
        payload: &Value,
        base_tag: Option<&str>,
    ) -> Result<Reminder> {
        let mut records = self.cloudkit()?.lookup(&[id.to_owned()]).await?;
        let mut record = records
            .pop()
            .ok_or_else(|| AppError::bad_request(format!("No such reminder: {id}")))?;
        let remote_tag = record.get("recordChangeTag").and_then(Value::as_str);
        if base_tag.is_some() && remote_tag != base_tag {
            return Err(AppError::Conflict {
                message: "This reminder changed on another device while you were editing it."
                    .into(),
                detail: record_to_reminder(&record)
                    .map(|r| serde_json::to_string(&r).unwrap_or_default())
                    .unwrap_or_default(),
            });
        }
        let patch = payload
            .as_object()
            .ok_or_else(|| AppError::bad_request("update payload must be an object"))?;
        let zone_for_write = cloudkit::text_field(&record, "TimeZone");
        let record_debug = record.to_string();
        let target = record
            .get_mut("fields")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| {
                AppError::internal("iCloud returned a reminder without fields", record_debug)
            })?;
        let mut tokens = Vec::new();
        for (json_name, cloud_name, token) in [
            ("title", "Title", "title"),
            ("description", "Description", "description"),
        ] {
            if let Some(value) = patch.get(json_name) {
                let text = value.as_str().unwrap_or("");
                let encoded = if target
                    .get(cloud_name)
                    .and_then(|v| v.get("type"))
                    .and_then(Value::as_str)
                    == Some("STRING")
                {
                    cloudkit::string(text)
                } else {
                    cloudkit::encrypted_bytes(text)
                };
                target.insert(cloud_name.into(), encoded);
                tokens.push(token);
            }
        }
        for (json_name, cloud_name, token) in [
            ("priority", "Priority", "priority"),
            ("completed", "Completed", "completed"),
            ("flagged", "Flagged", "flagged"),
            ("deleted", "Deleted", "deleted"),
            ("all_day", "AllDay", "allDay"),
        ] {
            if let Some(value) = patch.get(json_name) {
                target.insert(
                    cloud_name.into(),
                    cloudkit::int(
                        value
                            .as_i64()
                            .unwrap_or_else(|| value.as_bool().unwrap_or(false) as i64),
                    ),
                );
                tokens.push(token);
            }
        }
        if let Some(completed) = patch.get("completed").and_then(Value::as_bool) {
            if completed {
                target.insert(
                    "CompletionDate".into(),
                    cloudkit::timestamp(Utc::now().timestamp_millis()),
                );
            } else {
                target.remove("CompletionDate");
            }
        }
        if let Some(value) = patch.get("due_date") {
            if value.is_null() {
                target.remove("DueDate");
            } else if let Some(raw) = value.as_str() {
                target.insert(
                    "DueDate".into(),
                    cloudkit::timestamp(instant_to_floating_millis(
                        parse_instant(raw)?,
                        zone_for_write.as_deref(),
                    )),
                );
            }
            tokens.push("dueDate");
        }
        target.insert(
            "LastModifiedDate".into(),
            cloudkit::timestamp(Utc::now().timestamp_millis()),
        );
        target.insert(
            "ResolutionTokenMap".into(),
            cloudkit::resolution_tokens(&tokens),
        );
        let result = self
            .cloudkit()?
            .modify(vec![json!({"operationType":"update","record":record})])
            .await?;
        result.first().and_then(record_to_reminder).ok_or_else(|| {
            AppError::internal(
                "iCloud did not return the updated reminder",
                result.first().map(Value::to_string).unwrap_or_default(),
            )
        })
    }

    pub async fn delete(&self, id: &str, base_tag: Option<&str>) -> Result<Reminder> {
        self.update(id, &json!({"deleted":true}), base_tag).await
    }
}

fn record_to_reminder(record: &Value) -> Option<Reminder> {
    let id = record.get("recordName")?.as_str()?.to_owned();
    let time_zone = cloudkit::text_field(record, "TimeZone");
    let due_date = cloudkit::timestamp_field(record, "DueDate")
        .map(|ms| to_iso(floating_millis_to_instant(ms, time_zone.as_deref())));
    Some(Reminder {
        id,
        list_id: cloudkit::reference_field(record, "List").unwrap_or_default(),
        title: cloudkit::text_field(record, "Title").unwrap_or_default(),
        description: cloudkit::text_field(record, "Description").unwrap_or_default(),
        due_date,
        time_zone,
        priority: cloudkit::int_field(record, "Priority"),
        completed: cloudkit::int_field(record, "Completed") != 0,
        completed_date: real_timestamp(record, "CompletionDate"),
        flagged: cloudkit::int_field(record, "Flagged") != 0,
        all_day: cloudkit::int_field(record, "AllDay") != 0,
        deleted: cloudkit::int_field(record, "Deleted") != 0,
        created: real_timestamp(record, "CreationDate"),
        modified: real_timestamp(record, "LastModifiedDate"),
        change_tag: record
            .get("recordChangeTag")
            .and_then(Value::as_str)
            .map(str::to_owned),
        hashtag_ids: string_list(record, "HashtagIDs"),
        tags: Vec::new(),
        notified: 0,
        dirty: 0,
    })
}

fn real_timestamp(record: &Value, name: &str) -> Option<String> {
    cloudkit::timestamp_field(record, name)
        .and_then(DateTime::<Utc>::from_timestamp_millis)
        .map(to_iso)
}
fn string_list(record: &Value, name: &str) -> Vec<String> {
    cloudkit::field(record, name)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|v| {
                    v.as_str().map(str::to_owned).or_else(|| {
                        v.get("recordName")
                            .and_then(Value::as_str)
                            .map(str::to_owned)
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}
fn required_string<'a>(object: &'a Map<String, Value>, name: &str) -> Result<&'a str> {
    object
        .get(name)
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| AppError::bad_request(format!("missing {name}")))
}
fn parse_color(raw: Option<&str>) -> Option<String> {
    let value = raw?;
    if value.starts_with('#') {
        return Some(value.to_owned());
    }
    let parsed: Value = serde_json::from_str(value).ok()?;
    parsed
        .get("daHexString")
        .and_then(Value::as_str)
        .filter(|v| v.starts_with('#'))
        .map(str::to_owned)
}

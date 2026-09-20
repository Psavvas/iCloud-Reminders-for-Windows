use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct ReminderList {
    pub id: String,
    pub title: String,
    pub color_hex: Option<String>,
    pub count: i64,
    pub is_group: bool,
    #[serde(default)]
    pub position: i64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Reminder {
    pub id: String,
    pub list_id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub description: String,
    pub due_date: Option<String>,
    #[serde(default)]
    pub time_zone: Option<String>,
    #[serde(default)]
    pub priority: i64,
    #[serde(default)]
    pub completed: bool,
    pub completed_date: Option<String>,
    #[serde(default)]
    pub flagged: bool,
    #[serde(default)]
    pub all_day: bool,
    #[serde(default)]
    pub deleted: bool,
    pub created: Option<String>,
    pub modified: Option<String>,
    pub change_tag: Option<String>,
    #[serde(default)]
    pub hashtag_ids: Vec<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub notified: i64,
    #[serde(default)]
    pub dirty: i64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Tag {
    pub id: String,
    pub name: String,
    pub reminder_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct OutboxItem {
    pub seq: i64,
    pub reminder_id: String,
    pub op: String,
    pub payload: Value,
    pub base_tag: Option<String>,
    pub attempts: i64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Conflict {
    pub id: i64,
    pub reminder_id: String,
    pub local: Value,
    pub remote: Value,
    pub detected_at: String,
    pub resolved: bool,
}

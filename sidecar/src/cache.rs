use std::path::Path;
use std::sync::{Mutex, MutexGuard};

use chrono::Utc;
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, OptionalExtension, Row, params, params_from_iter};
use serde_json::{Map, Value, json};

use crate::error::{AppError, Result};
use crate::model::{Conflict, OutboxItem, Reminder, ReminderList, Tag};
use crate::timeutil::{local_day_bounds, to_iso};

pub const COMPLETED_LIMIT: i64 = 50;
pub const SCHEMA_VERSION: i64 = 2;

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS lists (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, color_hex TEXT,
  count INTEGER NOT NULL DEFAULT 0, is_group INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY, list_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', due_date TEXT,
  priority INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
  completed_date TEXT, flagged INTEGER NOT NULL DEFAULT 0,
  all_day INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
  created TEXT, modified TEXT, change_tag TEXT,
  notified INTEGER NOT NULL DEFAULT 0, dirty INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reminders_list ON reminders(list_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date)
  WHERE completed = 0 AND deleted = 0;
CREATE INDEX IF NOT EXISTS idx_reminders_completed ON reminders(completed_date DESC)
  WHERE completed = 1 AND deleted = 0;
CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, reminder_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tags_reminder ON tags(reminder_id);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE TABLE IF NOT EXISTS outbox (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, reminder_id TEXT NOT NULL,
  op TEXT NOT NULL, payload TEXT NOT NULL, base_tag TEXT,
  created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, reminder_id TEXT NOT NULL,
  local_json TEXT NOT NULL, remote_json TEXT NOT NULL,
  detected_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
);
"#;

pub struct Cache {
    connection: Mutex<Connection>,
}

#[derive(Default)]
pub struct ReminderQuery<'a> {
    pub list_id: Option<&'a str>,
    pub tag: Option<&'a str>,
    pub scope: Option<&'a str>,
    pub include_completed: bool,
    pub search: Option<&'a str>,
    pub sort: Option<&'a str>,
    pub limit: i64,
}

impl Cache {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                AppError::internal("Could not create the app data directory", e.to_string())
            })?;
        }
        let connection = Connection::open(path)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.execute_batch(SCHEMA)?;
        let cache = Self {
            connection: Mutex::new(connection),
        };
        cache.migrate()?;
        cache.set_meta("schema_version", Some(&SCHEMA_VERSION.to_string()))?;
        Ok(cache)
    }

    fn conn(&self) -> Result<MutexGuard<'_, Connection>> {
        self.connection
            .lock()
            .map_err(|_| AppError::internal("The local cache lock was poisoned", ""))
    }

    fn migrate(&self) -> Result<()> {
        let conn = self.conn()?;
        let has_completed_date = {
            let mut stmt = conn.prepare("PRAGMA table_info(reminders)")?;
            let names = stmt.query_map([], |row| row.get::<_, String>(1))?;
            names
                .filter_map(std::result::Result::ok)
                .any(|name| name == "completed_date")
        };
        if !has_completed_date {
            conn.execute("ALTER TABLE reminders ADD COLUMN completed_date TEXT", [])?;
        }
        Ok(())
    }

    pub fn get_meta(&self, key: &str) -> Result<Option<String>> {
        self.conn()?
            .query_row("SELECT value FROM meta WHERE key = ?1", [key], |row| {
                row.get(0)
            })
            .optional()
            .map_err(Into::into)
    }

    pub fn set_meta(&self, key: &str, value: Option<&str>) -> Result<()> {
        self.conn()?.execute(
            "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![key, value],
        )?;
        Ok(())
    }

    pub fn replace_lists(&self, lists: &[ReminderList]) -> Result<()> {
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        tx.execute("DELETE FROM lists", [])?;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO lists(id,title,color_hex,count,is_group,position) VALUES(?1,?2,?3,?4,?5,?6)",
            )?;
            for (position, list) in lists.iter().enumerate() {
                stmt.execute(params![
                    list.id,
                    list.title,
                    list.color_hex,
                    list.count,
                    list.is_group as i64,
                    position as i64
                ])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn lists(&self) -> Result<Vec<Value>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT l.*, (SELECT COUNT(*) FROM reminders r WHERE r.list_id=l.id AND r.completed=0 AND r.deleted=0) AS open_count FROM lists l ORDER BY l.position",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?, "title": row.get::<_, String>(1)?,
                "color_hex": row.get::<_, Option<String>>(2)?, "count": row.get::<_, i64>(3)?,
                "is_group": row.get::<_, i64>(4)?, "position": row.get::<_, i64>(5)?,
                "open_count": row.get::<_, i64>(6)?,
            }))
        })?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn upsert_reminders(&self, reminders: &[Reminder]) -> Result<usize> {
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        let mut changed = 0;
        for reminder in reminders {
            let existing = tx
                .query_row(
                    "SELECT notified,dirty FROM reminders WHERE id=?1",
                    [&reminder.id],
                    |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)),
                )
                .optional()?;
            if matches!(existing, Some((_, 1))) {
                continue;
            }
            let notified = existing.map(|value| value.0).unwrap_or(0);
            tx.execute(
                "INSERT INTO reminders(id,list_id,title,description,due_date,priority,completed,completed_date,flagged,all_day,deleted,created,modified,change_tag,notified,dirty)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,0)
                 ON CONFLICT(id) DO UPDATE SET list_id=excluded.list_id,title=excluded.title,description=excluded.description,due_date=excluded.due_date,priority=excluded.priority,completed=excluded.completed,completed_date=excluded.completed_date,flagged=excluded.flagged,all_day=excluded.all_day,deleted=excluded.deleted,modified=excluded.modified,change_tag=excluded.change_tag,notified=excluded.notified",
                params![
                    reminder.id, reminder.list_id, reminder.title, reminder.description,
                    reminder.due_date, reminder.priority, reminder.completed as i64,
                    reminder.completed_date, reminder.flagged as i64, reminder.all_day as i64,
                    reminder.deleted as i64, reminder.created, reminder.modified,
                    reminder.change_tag, notified
                ],
            )?;
            changed += 1;
        }
        tx.commit()?;
        Ok(changed)
    }

    fn order_clause(sort: Option<&str>, scope: Option<&str>) -> &'static str {
        if scope == Some("completed") && matches!(sort, None | Some("") | Some("manual")) {
            return "r.completed_date DESC NULLS LAST, r.modified DESC";
        }
        match sort.unwrap_or("manual") {
            "title" => {
                "r.title COLLATE NOCASE, CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date"
            }
            "title_desc" => "r.title COLLATE NOCASE DESC",
            "due" => {
                "CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date, r.title COLLATE NOCASE"
            }
            "due_desc" => "CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date DESC",
            "priority" => {
                "CASE r.priority WHEN 1 THEN 0 WHEN 5 THEN 1 WHEN 9 THEN 2 ELSE 3 END, CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date"
            }
            "created" => "r.created DESC",
            "created_asc" => "r.created",
            _ => {
                "r.completed, CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date, r.title COLLATE NOCASE"
            }
        }
    }

    pub fn reminders(&self, query: ReminderQuery<'_>) -> Result<Vec<Reminder>> {
        let mut sql = String::from("SELECT r.* FROM reminders r ");
        let mut args: Vec<SqlValue> = Vec::new();
        if let Some(tag) = query.tag {
            sql.push_str("JOIN tags t ON t.reminder_id=r.id AND t.name=? ");
            args.push(SqlValue::Text(tag.to_owned()));
        }
        sql.push_str(if query.scope == Some("deleted") {
            "WHERE r.deleted=1 "
        } else {
            "WHERE r.deleted=0 "
        });
        let (_, tomorrow) = local_day_bounds(Utc::now());
        match query.scope {
            Some("today") => {
                sql.push_str("AND r.completed=0 AND r.due_date IS NOT NULL AND r.due_date<? ");
                args.push(to_iso(tomorrow).into());
            }
            Some("upcoming") => {
                sql.push_str("AND r.completed=0 AND r.due_date IS NOT NULL AND r.due_date>=? ");
                args.push(to_iso(tomorrow).into());
            }
            Some("completed") => sql.push_str("AND r.completed=1 "),
            Some("deleted") => {}
            _ if !query.include_completed => sql.push_str("AND r.completed=0 "),
            _ => {}
        }
        if let Some(list_id) = query.list_id {
            sql.push_str("AND r.list_id=? ");
            args.push(SqlValue::Text(list_id.to_owned()));
        }
        if let Some(search) = query.search.filter(|value| !value.is_empty()) {
            sql.push_str("AND (r.title LIKE ? OR r.description LIKE ?) ");
            let pattern = format!("%{search}%");
            args.push(pattern.clone().into());
            args.push(pattern.into());
        }
        sql.push_str("ORDER BY ");
        sql.push_str(Self::order_clause(query.sort, query.scope));
        sql.push_str(" LIMIT ?");
        let limit = if query.scope == Some("completed") {
            query.limit.clamp(1, COMPLETED_LIMIT)
        } else {
            query.limit.clamp(1, 10_000)
        };
        args.push(limit.into());

        let conn = self.conn()?;
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(args), Self::row_to_reminder)?;
        let mut reminders = rows.collect::<std::result::Result<Vec<_>, _>>()?;
        Self::attach_tags(&conn, &mut reminders)?;
        Ok(reminders)
    }

    fn row_to_reminder(row: &Row<'_>) -> rusqlite::Result<Reminder> {
        Ok(Reminder {
            id: row.get("id")?,
            list_id: row.get("list_id")?,
            title: row.get("title")?,
            description: row.get("description")?,
            due_date: row.get("due_date")?,
            time_zone: None,
            priority: row.get("priority")?,
            completed: row.get::<_, i64>("completed")? != 0,
            completed_date: row.get("completed_date")?,
            flagged: row.get::<_, i64>("flagged")? != 0,
            all_day: row.get::<_, i64>("all_day")? != 0,
            deleted: row.get::<_, i64>("deleted")? != 0,
            created: row.get("created")?,
            modified: row.get("modified")?,
            change_tag: row.get("change_tag")?,
            hashtag_ids: Vec::new(),
            tags: Vec::new(),
            notified: row.get("notified")?,
            dirty: row.get("dirty")?,
        })
    }

    fn attach_tags(conn: &Connection, reminders: &mut [Reminder]) -> Result<()> {
        let mut stmt = conn.prepare("SELECT name FROM tags WHERE reminder_id=?1 ORDER BY name")?;
        for reminder in reminders {
            let names = stmt.query_map([&reminder.id], |row| row.get::<_, String>(0))?;
            reminder.tags = names.collect::<std::result::Result<Vec<_>, _>>()?;
        }
        Ok(())
    }

    pub fn reminder(&self, id: &str) -> Result<Option<Reminder>> {
        let conn = self.conn()?;
        let mut reminder = conn
            .query_row(
                "SELECT * FROM reminders WHERE id=?1",
                [id],
                Self::row_to_reminder,
            )
            .optional()?;
        if let Some(value) = reminder.as_mut() {
            Self::attach_tags(&conn, std::slice::from_mut(value))?;
        }
        Ok(reminder)
    }

    pub fn apply_local_edit(&self, id: &str, fields: &Map<String, Value>) -> Result<()> {
        const ALLOWED: &[&str] = &[
            "title",
            "description",
            "due_date",
            "priority",
            "completed",
            "flagged",
            "list_id",
            "deleted",
            "all_day",
        ];
        let selected: Vec<_> = fields
            .iter()
            .filter(|(key, _)| ALLOWED.contains(&key.as_str()))
            .collect();
        if selected.is_empty() {
            return Ok(());
        }
        let mut sql = String::from("UPDATE reminders SET ");
        let mut args = Vec::new();
        for (index, (key, value)) in selected.iter().enumerate() {
            if index > 0 {
                sql.push(',');
            }
            sql.push_str(key);
            sql.push_str("=?");
            args.push(json_to_sql(value));
        }
        sql.push_str(",modified=?,dirty=1 WHERE id=?");
        args.push(to_iso(Utc::now()).into());
        args.push(SqlValue::Text(id.to_owned()));
        self.conn()?.execute(&sql, params_from_iter(args))?;
        Ok(())
    }

    pub fn insert_local_reminder(&self, reminder: &Reminder) -> Result<()> {
        let now = to_iso(Utc::now());
        self.conn()?.execute(
            "INSERT INTO reminders(id,list_id,title,description,due_date,priority,completed,flagged,all_day,deleted,created,modified,change_tag,notified,dirty) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,0,?10,?10,NULL,0,1)",
            params![reminder.id,reminder.list_id,reminder.title,reminder.description,reminder.due_date,reminder.priority,reminder.completed as i64,reminder.flagged as i64,reminder.all_day as i64,now],
        )?;
        Ok(())
    }

    pub fn clear_dirty(&self, id: &str, change_tag: Option<&str>) -> Result<()> {
        self.conn()?.execute(
            "UPDATE reminders SET dirty=0,change_tag=?1 WHERE id=?2",
            params![change_tag, id],
        )?;
        Ok(())
    }

    pub fn replace_id(&self, old_id: &str, new_id: &str) -> Result<()> {
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        tx.execute(
            "UPDATE reminders SET id=?1 WHERE id=?2",
            params![new_id, old_id],
        )?;
        tx.execute(
            "UPDATE tags SET reminder_id=?1 WHERE reminder_id=?2",
            params![new_id, old_id],
        )?;
        tx.execute(
            "UPDATE outbox SET reminder_id=?1 WHERE reminder_id=?2",
            params![new_id, old_id],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn replace_tags_for(&self, reminder_id: &str, tags: &[Tag]) -> Result<()> {
        let mut conn = self.conn()?;
        let tx = conn.transaction()?;
        tx.execute("DELETE FROM tags WHERE reminder_id=?1", [reminder_id])?;
        {
            let mut stmt =
                tx.prepare("INSERT OR REPLACE INTO tags(id,name,reminder_id) VALUES(?1,?2,?3)")?;
            for tag in tags {
                stmt.execute(params![tag.id, tag.name, reminder_id])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn all_tags(&self) -> Result<Vec<Value>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT name,COUNT(*) FROM tags t JOIN reminders r ON r.id=t.reminder_id WHERE r.deleted=0 AND r.completed=0 GROUP BY name ORDER BY name")?;
        let rows = stmt.query_map([], |row| {
            Ok(json!({"name": row.get::<_,String>(0)?, "n": row.get::<_,i64>(1)?}))
        })?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn smart_counts(&self) -> Result<Value> {
        let (_, tomorrow) = local_day_bounds(Utc::now());
        let end = to_iso(tomorrow);
        let conn = self.conn()?;
        let count = |sql: &str, args: &[&dyn rusqlite::ToSql]| -> Result<i64> {
            Ok(conn.query_row(sql, args, |row| row.get(0))?)
        };
        Ok(json!({
            "today": count("SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=0 AND due_date IS NOT NULL AND due_date<?1", &[&end])?,
            "upcoming": count("SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=0 AND due_date IS NOT NULL AND due_date>=?1", &[&end])?,
            "completed": count("SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=1", &[])?,
            "deleted": count("SELECT COUNT(*) FROM reminders WHERE deleted=1", &[])?,
            "all": count("SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=0", &[])?,
        }))
    }

    pub fn settings(&self) -> Result<Value> {
        let mut settings = json!({
            "theme":"system", "sync_minutes":10, "notifications_enabled":true,
            "stale_after_minutes":60, "max_individual_toasts":3,
            "default_list_id":Value::Null, "search_scope":"list", "onboarded":false,
            "print_group_by":"due", "print_include_notes":true,
            "print_include_completed":false, "remember_password":true, "sort_by":{}
        });
        if let Some(raw) = self.get_meta("settings")? {
            if let Ok(Value::Object(stored)) = serde_json::from_str::<Value>(&raw) {
                if let Value::Object(defaults) = &mut settings {
                    for (key, value) in stored {
                        if defaults.contains_key(&key) {
                            defaults.insert(key, value);
                        }
                    }
                }
            }
        }
        Ok(settings)
    }

    pub fn set_settings(&self, patch: &Map<String, Value>) -> Result<Value> {
        let mut settings = self.settings()?;
        if let Value::Object(current) = &mut settings {
            for (key, value) in patch {
                if current.contains_key(key) {
                    current.insert(key.clone(), value.clone());
                }
            }
        }
        self.set_meta("settings", Some(&serde_json::to_string(&settings)?))?;
        Ok(settings)
    }

    pub fn due_unnotified(&self, now_iso: &str) -> Result<Vec<Reminder>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT * FROM reminders WHERE deleted=0 AND completed=0 AND notified=0 AND due_date IS NOT NULL AND due_date<=?1 ORDER BY due_date")?;
        let rows = stmt.query_map([now_iso], Self::row_to_reminder)?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn mark_notified(&self, ids: &[String]) -> Result<()> {
        if ids.is_empty() {
            return Ok(());
        }
        let marks = std::iter::repeat_n("?", ids.len())
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!("UPDATE reminders SET notified=1 WHERE id IN ({marks})");
        self.conn()?.execute(&sql, params_from_iter(ids))?;
        Ok(())
    }

    pub fn enqueue(
        &self,
        reminder_id: &str,
        op: &str,
        payload: &Value,
        base_tag: Option<&str>,
    ) -> Result<()> {
        self.conn()?.execute(
            "INSERT INTO outbox(reminder_id,op,payload,base_tag,created_at) VALUES(?1,?2,?3,?4,?5)",
            params![
                reminder_id,
                op,
                serde_json::to_string(payload)?,
                base_tag,
                to_iso(Utc::now())
            ],
        )?;
        Ok(())
    }

    pub fn pending(&self, limit: i64) -> Result<Vec<OutboxItem>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT seq,reminder_id,op,payload,base_tag,attempts FROM outbox ORDER BY seq LIMIT ?1",
        )?;
        let rows = stmt.query_map([limit], |row| {
            let raw: String = row.get(3)?;
            Ok(OutboxItem {
                seq: row.get(0)?,
                reminder_id: row.get(1)?,
                op: row.get(2)?,
                payload: serde_json::from_str(&raw).unwrap_or(Value::Null),
                base_tag: row.get(4)?,
                attempts: row.get(5)?,
            })
        })?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn dequeue(&self, seq: i64) -> Result<()> {
        self.conn()?
            .execute("DELETE FROM outbox WHERE seq=?1", [seq])?;
        Ok(())
    }
    pub fn record_failure(&self, seq: i64, error: &str) -> Result<()> {
        self.conn()?.execute(
            "UPDATE outbox SET attempts=attempts+1,last_error=?1 WHERE seq=?2",
            params![error, seq],
        )?;
        Ok(())
    }

    pub fn record_conflict(&self, reminder_id: &str, local: &Value, remote: &Value) -> Result<()> {
        self.conn()?.execute("INSERT INTO conflicts(reminder_id,local_json,remote_json,detected_at) VALUES(?1,?2,?3,?4)", params![reminder_id,serde_json::to_string(local)?,serde_json::to_string(remote)?,to_iso(Utc::now())])?;
        Ok(())
    }

    pub fn conflicts(&self) -> Result<Vec<Conflict>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT id,reminder_id,local_json,remote_json,detected_at,resolved FROM conflicts WHERE resolved=0 ORDER BY detected_at DESC")?;
        let rows = stmt.query_map([], |row| {
            let local: String = row.get(2)?;
            let remote: String = row.get(3)?;
            Ok(Conflict {
                id: row.get(0)?,
                reminder_id: row.get(1)?,
                local: serde_json::from_str(&local).unwrap_or(Value::Null),
                remote: serde_json::from_str(&remote).unwrap_or(Value::Null),
                detected_at: row.get(4)?,
                resolved: row.get::<_, i64>(5)? != 0,
            })
        })?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn resolve_conflict(&self, id: i64) -> Result<()> {
        self.conn()?
            .execute("UPDATE conflicts SET resolved=1 WHERE id=?1", [id])?;
        Ok(())
    }
}

fn json_to_sql(value: &Value) -> SqlValue {
    match value {
        Value::Null => SqlValue::Null,
        Value::Bool(value) => SqlValue::Integer(*value as i64),
        Value::Number(value) => value
            .as_i64()
            .map(SqlValue::Integer)
            .or_else(|| value.as_f64().map(SqlValue::Real))
            .unwrap_or(SqlValue::Null),
        Value::String(value) => SqlValue::Text(value.clone()),
        other => SqlValue::Text(other.to_string()),
    }
}

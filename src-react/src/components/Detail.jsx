import { useEffect, useState } from "react";
import { PRIORITIES, formatDue, reshapeDue, toLocalInput } from "../format.js";

export default function Detail({ reminder, lists, onSave, onDelete }) {
  const [draft, setDraft] = useState(null);

  // Re-seed when a different reminder is selected, or when sync changes this
  // one underneath us; keying on the change tag catches the latter.
  useEffect(() => {
    setDraft(
      reminder
        ? {
            title: reminder.title || "",
            description: reminder.description || "",
            due: reshapeDue(toLocalInput(reminder.due_date), !!reminder.all_day),
            allDay: !!reminder.all_day,
            priority: String(Number(reminder.priority) || 0),
          }
        : null
    );
  }, [reminder?.id, reminder?.change_tag]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!reminder || !draft) {
    return (
      <section className="detail">
        <p className="empty">Select a reminder.</p>
      </section>
    );
  }

  const list = lists.find((l) => l.id === reminder.list_id);
  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <section className="detail">
      <div className="detail-body" key={reminder.id}>
        <label htmlFor="d-title">Title</label>
        <input
          id="d-title"
          value={draft.title}
          onChange={(e) => set("title", e.target.value)}
        />

        <label htmlFor="d-desc">Notes</label>
        <textarea
          id="d-desc"
          rows={6}
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
        />

        <label htmlFor="d-due">Due</label>
        <div className="row">
          <input
            id="d-due"
            type={draft.allDay ? "date" : "datetime-local"}
            value={draft.due}
            onChange={(e) => set("due", e.target.value)}
          />
          <button className="ghost" type="button" onClick={() => set("due", "")}>
            Clear
          </button>
        </div>
        <label className="switch-row inline-switch">
          <span>All day</span>
          <input
            type="checkbox"
            checked={draft.allDay}
            onChange={(e) => {
              const on = e.target.checked;
              // Type and value change in one commit; separately, the browser
              // rejects the value it does not recognise and blanks the field.
              setDraft((d) => ({ ...d, allDay: on, due: reshapeDue(d.due, on) }));
            }}
          />
        </label>

        <label htmlFor="d-prio">Priority</label>
        <select
          id="d-prio"
          value={draft.priority}
          onChange={(e) => set("priority", e.target.value)}
        >
          {PRIORITIES.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>

        <label>List</label>
        <div className="static">{list ? list.title : reminder.list_id}</div>

        <label>Tags</label>
        <div className="static">
          {(reminder.tags || []).map((t) => `#${t}`).join("  ") || "—"}
        </div>

        <div className="detail-actions">
          <button
            className="primary"
            type="button"
            onClick={() =>
              onSave({
                id: reminder.id,
                title: draft.title,
                description: draft.description,
                // datetime-local has no zone; the sidecar resolves it against
                // the local zone rather than letting Apple read it as UTC. An
                // all-day value is a bare date, and gets snapped to midnight
                // there, so it is sent as-is.
                due_date: draft.due
                  ? draft.allDay
                    ? draft.due
                    : draft.due + ":00"
                  : null,
                all_day: draft.allDay,
                priority: Number(draft.priority),
              })
            }
          >
            Save
          </button>
          <button className="danger" type="button" onClick={() => onDelete(reminder)}>
            Delete
          </button>
        </div>

        <p className="hint tiny">
          {reminder.dirty
            ? "Pending sync to iCloud."
            : reminder.modified
            ? `Last changed ${formatDue(reminder.modified)}`
            : ""}
        </p>
      </div>
    </section>
  );
}

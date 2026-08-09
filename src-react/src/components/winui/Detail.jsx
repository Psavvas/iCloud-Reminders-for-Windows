import { useEffect, useState } from "react";
import { PRIORITIES, formatDue, reshapeDue, toLocalInput } from "../../format.js";
import { DeletedIcon } from "./icons.jsx";

/**
 * The detail pane as a stack of settings cards, which is how Windows presents
 * a list of properties: a label and its control on one row, each row its own
 * bordered surface, rather than Apple's inset group of borderless fields.
 */
function Card({ label, htmlFor, hint, children, stacked }) {
  return (
    <div className={`win-card${stacked ? " stacked" : ""}`}>
      <div className="win-card-text">
        <label htmlFor={htmlFor}>{label}</label>
        {hint ? <span className="win-card-hint">{hint}</span> : null}
      </div>
      <div className="win-card-control">{children}</div>
    </div>
  );
}

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
      <section className="win-detail">
        <p className="win-empty">Select a reminder to see its details.</p>
      </section>
    );
  }

  const list = lists.find((l) => l.id === reminder.list_id);
  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <section className="win-detail">
      <div className="win-detail-body" key={reminder.id}>
        <h3 className="win-detail-head">Details</h3>

        <Card label="Title" htmlFor="d-title" stacked>
          <input
            id="d-title"
            value={draft.title}
            onChange={(e) => set("title", e.target.value)}
          />
        </Card>

        <Card label="Notes" htmlFor="d-desc" stacked>
          <textarea
            id="d-desc"
            rows={5}
            value={draft.description}
            onChange={(e) => set("description", e.target.value)}
          />
        </Card>

        <Card label="All day" hint="A date with no time of day">
          <label className="win-switch">
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
            <span className="win-switch-track" aria-hidden="true" />
          </label>
        </Card>

        <Card label="Due" htmlFor="d-due" stacked>
          <div className="win-row-controls">
            <input
              id="d-due"
              type={draft.allDay ? "date" : "datetime-local"}
              value={draft.due}
              onChange={(e) => set("due", e.target.value)}
            />
            <button className="win-btn" type="button" onClick={() => set("due", "")}>
              Clear
            </button>
          </div>
        </Card>

        <Card label="Priority" htmlFor="d-prio">
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
        </Card>

        <Card label="List">
          <span className="win-static">
            {list ? (
              <>
                <span
                  className="win-badge-dot"
                  style={{ background: list.color_hex || "#8E8E93" }}
                />
                {list.title}
              </>
            ) : (
              reminder.list_id
            )}
          </span>
        </Card>

        <Card label="Tags" hint="Read-only — Apple never renders tags written here" stacked>
          <span className="win-static">
            {(reminder.tags || []).map((t) => `#${t}`).join("  ") || "None"}
          </span>
        </Card>

        <div className="win-detail-actions">
          <button
            className="win-btn accent"
            type="button"
            onClick={() =>
              onSave({
                id: reminder.id,
                title: draft.title,
                description: draft.description,
                // datetime-local has no zone; the sidecar resolves it against
                // the local zone rather than letting Apple read it as UTC. An
                // all-day value is a bare date and gets snapped to midnight
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
          <button
            className="win-btn danger"
            type="button"
            onClick={() => onDelete(reminder)}
          >
            <DeletedIcon />
            Delete
          </button>
        </div>

        <p className="win-hint">
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

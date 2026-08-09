import { useEffect, useState } from "react";
import { PRIORITIES, formatDue, reshapeDue, toLocalInput } from "../../format.js";

/**
 * The detail pane as a WinUI form: a caption above each control, related
 * controls paired across two columns, and one primary button at the bottom
 * that stays disabled until something has actually changed.
 *
 * This was a stack of bordered settings cards, which is the right pattern for
 * a Settings page and the wrong one here -- a card per field turns eight
 * properties into eight surfaces, and the pane is 340px wide. Windows reserves
 * that treatment for settings and uses plain captioned fields for editing.
 */

/** A caption and its control. Full width unless it is in a `.win-pair`. */
function Field({ label, htmlFor, action, children }) {
  return (
    <div className="win-field">
      <div className="win-field-head">
        <label htmlFor={htmlFor}>{label}</label>
        {action}
      </div>
      {children}
    </div>
  );
}

function Toggle({ label, checked, onChange }) {
  return (
    <div className="win-field">
      <span className="win-field-label">{label}</span>
      <label className="win-switch">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="win-switch-track" aria-hidden="true" />
        <span className="win-switch-text">{checked ? "On" : "Off"}</span>
      </label>
    </div>
  );
}

const seed = (r) => ({
  title: r.title || "",
  description: r.description || "",
  due: reshapeDue(toLocalInput(r.due_date), !!r.all_day),
  allDay: !!r.all_day,
  flagged: !!r.flagged,
  priority: String(Number(r.priority) || 0),
});

export default function Detail({ reminder, lists, onSave, onDelete }) {
  const [draft, setDraft] = useState(null);
  const [clean, setClean] = useState(null);

  // Re-seed when a different reminder is selected, or when sync changes this
  // one underneath us; keying on the change tag catches the latter. The
  // pristine copy is kept alongside so the Save button can tell whether there
  // is anything to save.
  useEffect(() => {
    const next = reminder ? seed(reminder) : null;
    setDraft(next);
    setClean(next);
  }, [reminder?.id, reminder?.change_tag]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!reminder || !draft) {
    return (
      <section className="win-detail">
        <div className="win-detail-body">
          <h2 className="win-detail-head">Details</h2>
          <p className="win-empty">Select a reminder to see its details.</p>
        </div>
      </section>
    );
  }

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));
  const dirty = JSON.stringify(draft) !== JSON.stringify(clean);
  const list = lists.find((l) => l.id === reminder.list_id);

  return (
    <section className="win-detail">
      <div className="win-detail-body" key={reminder.id}>
        <h2 className="win-detail-head">Details</h2>

        <Field label="Title" htmlFor="d-title">
          <input
            id="d-title"
            value={draft.title}
            onChange={(e) => set("title", e.target.value)}
          />
        </Field>

        <Field label="Notes" htmlFor="d-desc">
          <textarea
            id="d-desc"
            rows={4}
            placeholder="Add a note"
            value={draft.description}
            onChange={(e) => set("description", e.target.value)}
          />
        </Field>

        <div className="win-pair">
          {/* Read-only, and not a combo box. Moving a reminder between lists
              is not something this API does -- offering the control and then
              dropping the change on the floor would be worse than saying so. */}
          <Field label="List">
            <div className="win-readonly">
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
            </div>
          </Field>
          <Field label="Priority" htmlFor="d-prio">
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
          </Field>
        </div>

        <Field
          label="Due date"
          htmlFor="d-due"
          action={
            draft.due ? (
              <button className="win-link" type="button" onClick={() => set("due", "")}>
                Clear
              </button>
            ) : null
          }
        >
          <input
            id="d-due"
            /* One control when there is a time to set and a date-only one when
               there is not: an all-day reminder has no clock, so it must not
               be handed an empty one to fill in. */
            type={draft.allDay ? "date" : "datetime-local"}
            value={draft.due}
            onChange={(e) => set("due", e.target.value)}
          />
        </Field>

        <div className="win-pair">
          <Toggle
            label="All day"
            checked={draft.allDay}
            onChange={(on) =>
              // Type and value change in one commit; separately, the browser
              // rejects the value it does not recognise and blanks the field.
              setDraft((d) => ({ ...d, allDay: on, due: reshapeDue(d.due, on) }))
            }
          />
          <Toggle
            label="Flagged"
            checked={draft.flagged}
            onChange={(on) => set("flagged", on)}
          />
        </div>

        <Field label="Tags">
          <div className="win-readonly">
            {(reminder.tags || []).map((t) => `#${t}`).join("  ") || "None"}
            <span className="win-field-hint">
              Read-only — Apple never renders tags written here.
            </span>
          </div>
        </Field>

        <div className="win-detail-actions">
          <button
            className="win-btn accent"
            type="button"
            /* Nothing to save is not the same as a button that saves nothing.
               Windows disables the commit until there is a change to commit. */
            disabled={!dirty}
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
                flagged: draft.flagged,
                priority: Number(draft.priority),
              })
            }
          >
            Save changes
          </button>
          <button className="win-btn" type="button" onClick={() => onDelete(reminder)}>
            Delete
          </button>
        </div>

        <p className="win-field-hint">
          {reminder.dirty
            ? "Waiting to sync to iCloud."
            : reminder.modified
            ? `Last changed ${formatDue(reminder.modified)}`
            : ""}
        </p>
      </div>
    </section>
  );
}

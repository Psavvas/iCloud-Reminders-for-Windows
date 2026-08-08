import { useEffect, useRef, useState } from "react";
import Sheet from "./Sheet.jsx";
import { PRIORITIES, reshapeDue } from "../format.js";

export default function NewReminderSheet({
  lists, defaultListId, initialTitle = "", onClose, onCreate,
}) {
  const usable = lists.filter((l) => !l.is_group);
  const fallback =
    defaultListId ||
    (usable.find((l) => (l.title || "").toLowerCase() === "inbox") || usable[0] || {}).id;

  const [form, setForm] = useState({
    title: initialTitle,
    notes: "",
    listId: fallback || "",
    priority: "0",
    due: "",
    allDay: false,
  });
  const titleRef = useRef(null);
  const dueRef = useRef(null);
  // With a title already carried in from the composer, putting the caret back
  // on it means retyping past it; the point of Details is the rest.
  useEffect(() => {
    if (initialTitle) dueRef.current?.focus();
    else titleRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const submit = (e) => {
    e.preventDefault();
    if (!form.title.trim() || !form.listId) return;
    onCreate({
      list_id: form.listId,
      title: form.title.trim(),
      description: form.notes,
      // A date input yields "2026-08-07"; datetime-local yields
      // "2026-08-07T14:30". The sidecar snaps an all-day one to local midnight,
      // so the exact time sent with it does not matter.
      due_date: form.due ? (form.allDay ? form.due : form.due + ":00") : null,
      all_day: form.allDay,
      priority: Number(form.priority),
    });
  };

  return (
    <Sheet onClose={onClose}>
      <form onSubmit={submit}>
        <h3>New Reminder</h3>

        <label htmlFor="n-title">Title</label>
        <input
          id="n-title"
          ref={titleRef}
          autoComplete="off"
          placeholder="What needs doing?"
          value={form.title}
          onChange={(e) => set("title", e.target.value)}
          required
        />

        <label htmlFor="n-notes">Notes</label>
        <textarea
          id="n-notes"
          rows={3}
          placeholder="Optional"
          value={form.notes}
          onChange={(e) => set("notes", e.target.value)}
        />

        <div className="sheet-grid">
          <div>
            <label htmlFor="n-list">List</label>
            <select
              id="n-list"
              value={form.listId}
              onChange={(e) => set("listId", e.target.value)}
            >
              {usable.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.title}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="n-prio">Priority</label>
            <select
              id="n-prio"
              value={form.priority}
              onChange={(e) => set("priority", e.target.value)}
            >
              {PRIORITIES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <label htmlFor="n-due">Due</label>
        <div className="row">
          <input
            id="n-due"
            ref={dueRef}
            type={form.allDay ? "date" : "datetime-local"}
            value={form.due}
            onChange={(e) => set("due", e.target.value)}
          />
          <button type="button" className="ghost" onClick={() => set("due", "")}>
            Clear
          </button>
        </div>
        <label className="switch-row inline-switch">
          <span>All day</span>
          <input
            type="checkbox"
            checked={form.allDay}
            onChange={(e) => {
              const on = e.target.checked;
              // Reshape together: the input type and its value have to change
              // in the same commit or the browser discards the value.
              setForm((f) => ({ ...f, allDay: on, due: reshapeDue(f.due, on) }));
            }}
          />
        </label>

        <div className="sheet-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary">
            Add Reminder
          </button>
        </div>
      </form>
    </Sheet>
  );
}

import { useEffect, useRef, useState } from "react";
import Sheet from "./Sheet.jsx";
import { PRIORITIES } from "../format.js";

export default function NewReminderSheet({ lists, defaultListId, onClose, onCreate }) {
  const usable = lists.filter((l) => !l.is_group);
  const fallback =
    defaultListId ||
    (usable.find((l) => (l.title || "").toLowerCase() === "inbox") || usable[0] || {}).id;

  const [form, setForm] = useState({
    title: "",
    notes: "",
    listId: fallback || "",
    priority: "0",
    due: "",
  });
  const titleRef = useRef(null);
  useEffect(() => titleRef.current?.focus(), []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const submit = (e) => {
    e.preventDefault();
    if (!form.title.trim() || !form.listId) return;
    onCreate({
      list_id: form.listId,
      title: form.title.trim(),
      description: form.notes,
      due_date: form.due ? form.due + ":00" : null,
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
            type="datetime-local"
            value={form.due}
            onChange={(e) => set("due", e.target.value)}
          />
          <button type="button" className="ghost" onClick={() => set("due", "")}>
            Clear
          </button>
        </div>

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

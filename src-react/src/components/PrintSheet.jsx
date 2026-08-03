import { useState } from "react";
import Sheet from "./Sheet.jsx";
import { call } from "../bridge.js";
import { dayHeading, formatDue, priorityLabel, priorityMarks } from "../format.js";

const GROUPS = [
  { value: "due", label: "Due date" },
  { value: "priority", label: "Priority" },
  { value: "list", label: "List" },
  { value: "none", label: "Current order" },
];

function groupOf(r, mode, lists) {
  if (mode === "due") {
    const h = dayHeading(r.due_date);
    return { key: String(h.order), label: h.label };
  }
  if (mode === "priority") {
    const p = Number(r.priority) || 0;
    return (
      { 1: { key: "0", label: "High" }, 5: { key: "1", label: "Medium" },
        9: { key: "2", label: "Low" } }[p] || { key: "3", label: "No priority" }
    );
  }
  if (mode === "list") {
    const l = lists.find((x) => x.id === r.list_id);
    return { key: (l && l.title) || "zzz", label: (l && l.title) || "Unknown list" };
  }
  return { key: "", label: "" };
}

export default function PrintSheet({ settings, setSettings, title, lists, query, onClose }) {
  const [groupBy, setGroupBy] = useState(settings.print_group_by || "due");
  const [notes, setNotes] = useState(settings.print_include_notes !== false);
  const [completed, setCompleted] = useState(!!settings.print_include_completed);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setSettings(
      await call("set_settings", {
        print_group_by: groupBy,
        print_include_notes: notes,
        print_include_completed: completed,
      })
    );

    // Re-query rather than reuse what's on screen: printing usually wants a
    // different set, completed items in particular.
    const rows = await call("reminders", {
      ...query,
      include_completed: completed,
      limit: 5000,
    });

    const buckets = new Map();
    for (const r of rows) {
      const g = groupOf(r, groupBy, lists);
      const b = buckets.get(g.key) || { label: g.label, items: [] };
      b.items.push(r);
      buckets.set(g.key, b);
    }
    const ordered = [...buckets.entries()].sort((a, b) =>
      String(a[0]).localeCompare(String(b[0]), undefined, { numeric: true })
    );

    const view = document.getElementById("print-view");
    const esc = (t) =>
      String(t ?? "").replace(/[&<>"]/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
      );

    let html = `<header class="pv-head"><h1>${esc(title)}</h1><div class="pv-meta">
      <span>${rows.length} reminder${rows.length === 1 ? "" : "s"}</span>
      <span>Grouped by ${esc((GROUPS.find((g) => g.value === groupBy) || {}).label)}</span>
      <span>${esc(new Date().toLocaleString(undefined, { dateStyle: "long", timeStyle: "short" }))}</span>
      </div></header>`;

    if (!rows.length) html += `<p class="pv-empty">Nothing to print.</p>`;

    for (const [, g] of ordered) {
      if (g.label) html += `<h2 class="pv-group">${esc(g.label)}</h2>`;
      html += `<ul class="pv-list">`;
      for (const r of g.items) {
        const bits = [];
        if (r.due_date) bits.push(esc(formatDue(r.due_date, r.all_day)));
        if (priorityMarks(r.priority)) bits.push(esc(priorityLabel(r.priority)) + " priority");
        if (groupBy !== "list") {
          const l = lists.find((x) => x.id === r.list_id);
          if (l && !query.list_id) bits.push(esc(l.title));
        }
        for (const t of r.tags || []) bits.push("#" + esc(t));
        html += `<li class="pv-item${r.completed ? " done" : ""}">
          <span class="pv-box${r.completed ? " ticked" : ""}"></span>
          <div class="pv-body">
            <div class="pv-title">${esc(r.title || "(untitled)")}</div>
            ${bits.length ? `<div class="pv-sub">${bits.join(" · ")}</div>` : ""}
            ${notes && r.description ? `<div class="pv-notes">${esc(r.description)}</div>` : ""}
          </div></li>`;
      }
      html += `</ul>`;
    }
    view.innerHTML = html;

    onClose();
    // Let layout settle before the print dialog snapshots the page.
    requestAnimationFrame(() => setTimeout(() => window.print(), 60));
  };

  return (
    <Sheet onClose={onClose}>
      <h3>Print</h3>
      <p className="hint">Printing “{title}”.</p>

      <label htmlFor="p-group">Organise by</label>
      <select id="p-group" value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
        {GROUPS.map((g) => (
          <option key={g.value} value={g.value}>
            {g.label}
          </option>
        ))}
      </select>

      <label className="switch-row">
        <span>Include notes</span>
        <input type="checkbox" checked={notes} onChange={(e) => setNotes(e.target.checked)} />
      </label>
      <label className="switch-row">
        <span>Include completed</span>
        <input
          type="checkbox"
          checked={completed}
          onChange={(e) => setCompleted(e.target.checked)}
        />
      </label>

      <div className="sheet-actions">
        <button type="button" className="ghost" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="primary" onClick={run} disabled={busy}>
          {busy ? "Preparing…" : "Print"}
        </button>
      </div>
    </Sheet>
  );
}

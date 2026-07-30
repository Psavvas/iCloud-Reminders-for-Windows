import { useEffect, useRef } from "react";

export default function Sidebar({
  smart, counts, lists, tags, scope, listId, tag,
  onSelect, status, syncLine, onSync, onSettings,
}) {
  const activeRef = useRef(null);

  // With 14 lists the sidebar scrolls; keep the selected row on screen.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [listId, scope, tag]);

  const statusText = syncLine
    ? syncLine
    : [
        status.running
          ? "Syncing…"
          : status.last_sync
          ? `Synced ${new Date(status.last_sync).toLocaleTimeString(undefined, {
              hour: "2-digit",
              minute: "2-digit",
            })}`
          : "Not synced yet",
        status.pending_pushes ? `${status.pending_pushes} pending` : null,
      ]
        .filter(Boolean)
        .join(" · ");

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="brand">Reminders</span>
        <div className="head-actions">
          <button className="icon-btn" title="Sync now" onClick={onSync}>
            ⟳
          </button>
          <button className="icon-btn" title="Settings (Ctrl+,)" onClick={onSettings}>
            ⚙
          </button>
        </div>
      </div>

      <nav className="smart">
        {smart.map((s) => {
          const active = scope === s.key && !listId && !tag;
          return (
            <button
              key={s.key}
              ref={active ? activeRef : null}
              className={`row smart-row${active ? " active" : ""}`}
              onClick={() => onSelect({ scope: s.key })}
            >
              <span className="glyph" style={{ background: s.color }}>
                {s.glyph}
              </span>
              <span className="row-name">{s.label}</span>
              <span className="count">{counts[s.key] ?? ""}</span>
            </button>
          );
        })}
      </nav>

      <div className="section-label listy">Lists</div>
      <nav className="lists">
        {lists.map((l) => {
          const active = listId === l.id && !tag;
          return (
            <button
              key={l.id}
              ref={active ? activeRef : null}
              className={`row list-row${active ? " active" : ""}${
                l.is_group ? " group" : ""
              }`}
              onClick={() => onSelect({ listId: l.id })}
            >
              <span className="dot" style={{ background: l.color_hex || "#8E8E93" }} />
              <span className="row-name">{l.title}</span>
              <span className="count">{l.open_count ?? 0}</span>
            </button>
          );
        })}
      </nav>

      <div className="tag-section">
        <div className="section-label">Tags</div>
        <div className="tags">
          {tags.length === 0 && <span className="hint tiny">No tags yet.</span>}
          {tags.map((t) => (
            <button
              key={t.name}
              className={`chip${tag === t.name ? " active" : ""}`}
              onClick={() => onSelect(tag === t.name ? { scope: "today" } : { tag: t.name })}
            >
              #{t.name}
            </button>
          ))}
        </div>
        <p className="hint tiny">
          Tags are read-only — Apple's API accepts tag writes but the Reminders
          app never renders them.
        </p>
      </div>

      <div className="sync-status">{statusText}</div>
    </aside>
  );
}

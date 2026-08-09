import { useEffect, useRef, useState } from "react";
import SyncBar from "../SyncBar.jsx";
import {
  GroupIcon,
  ListIcon,
  MenuIcon,
  SMART_ICONS,
  SettingsIcon,
  SyncIcon,
} from "./icons.jsx";

/**
 * A NavigationView, which is what this pane is in a Windows app.
 *
 * Four things separate it from the Apple sidebar next door, and all four are
 * conventions rather than decoration: the pane collapses to a rail of icons
 * from a hamburger at the top; the selected item is marked by an accent bar at
 * its left edge rather than by a tinted pill; the icons are outlines in the
 * text colour rather than white artwork on coloured circles, with a list's own
 * colour reduced to a dot beside it; and Settings is a footer item in the pane
 * instead of a glyph in a title bar.
 */
export default function Sidebar({
  smart, counts, lists, tags, scope, listId, tag,
  onSelect, status, sync, onSync, onSettings,
}) {
  const activeRef = useRef(null);
  const [collapsed, setCollapsed] = useState(false);

  // With 14 lists the pane scrolls; keep the selected item on screen.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [listId, scope, tag]);

  const statusText = [
    status.last_sync
      ? `Synced ${new Date(status.last_sync).toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
        })}`
      : "Not synced yet",
    status.pending_pushes ? `${status.pending_pushes} pending` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const item = (key, { active, onClick, icon, name, count, dot, ref }) => (
    <button
      key={key}
      ref={ref}
      className={`nav-item${active ? " active" : ""}`}
      onClick={onClick}
      title={collapsed ? name : undefined}
      aria-current={active ? "page" : undefined}
    >
      <span className="nav-pip" aria-hidden="true" />
      <span className="nav-icon">{icon}</span>
      <span className="nav-name">{name}</span>
      {dot ? <span className="nav-dot" style={{ background: dot }} /> : null}
      <span className="nav-count">{count}</span>
    </button>
  );

  return (
    <aside className={`nav${collapsed ? " collapsed" : ""}`}>
      <div className="nav-head">
        <button
          className="nav-hamburger"
          title={collapsed ? "Open navigation" : "Close navigation"}
          aria-label="Toggle navigation"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((v) => !v)}
        >
          <MenuIcon />
        </button>
        <span className="nav-brand">Reminders</span>
      </div>

      {/* Outside the scroller on purpose. There are five of these and a dozen
          lists, and scrolling the selected list into view took Today and
          Upcoming off the top of the pane with it. */}
      <nav className="nav-items nav-fixed">
        {smart.map((s) => {
          const active = scope === s.key && !listId && !tag;
          const Glyph = SMART_ICONS[s.key];
          return item(s.key, {
            active,
            ref: active ? activeRef : null,
            onClick: () => onSelect({ scope: s.key }),
            icon: Glyph ? <Glyph /> : null,
            name: s.label,
            count: counts[s.key] ?? "",
          });
        })}
      </nav>

      <div className="nav-sep" />
      <div className="nav-group-label">Lists</div>

      <div className="nav-scroll">
        <nav className="nav-items">
          {lists.map((l) => {
            const active = listId === l.id && !tag;
            return item(l.id, {
              active,
              ref: active ? activeRef : null,
              onClick: () => onSelect({ listId: l.id }),
              icon: l.is_group ? <GroupIcon /> : <ListIcon />,
              name: l.title,
              // A list's colour is Apple's organising idea, not Windows's, so
              // it survives as a dot rather than as a filled tile.
              dot: l.is_group ? null : l.color_hex || "#8E8E93",
              // A group's own count is always zero -- its reminders live in
              // the lists inside it.
              count: l.is_group ? "" : l.open_count ?? 0,
            });
          })}
        </nav>

        {!collapsed && (
          <>
            <div className="nav-sep" />
            <div className="nav-group-label">Tags</div>
            <div className="nav-tags">
              {tags.length === 0 && <span className="win-hint">No tags yet.</span>}
              {tags.map((t) => (
                <button
                  key={t.name}
                  className={`win-chip${tag === t.name ? " active" : ""}`}
                  onClick={() =>
                    onSelect(tag === t.name ? { scope: "today" } : { tag: t.name })
                  }
                >
                  #{t.name}
                </button>
              ))}
            </div>
            <p className="win-hint nav-note">
              Tags are read-only — Apple's API accepts tag writes but the
              Reminders app never renders them.
            </p>
          </>
        )}
      </div>

      <div className="nav-foot">
        {sync ? <SyncBar sync={sync} /> : null}
        {!sync && !collapsed && <div className="nav-status">{statusText}</div>}
        <button
          className="nav-item"
          onClick={onSync}
          title="Sync now"
        >
          <span className="nav-pip" aria-hidden="true" />
          <span className={`nav-icon${sync ? " spinning" : ""}`}>
            <SyncIcon />
          </span>
          <span className="nav-name">Sync now</span>
        </button>
        <button
          className="nav-item"
          onClick={onSettings}
          title="Settings (Ctrl+,)"
        >
          <span className="nav-pip" aria-hidden="true" />
          <span className="nav-icon"><SettingsIcon /></span>
          <span className="nav-name">Settings</span>
        </button>
      </div>
    </aside>
  );
}

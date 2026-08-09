import { useEffect, useRef, useState } from "react";
import SyncBar from "../SyncBar.jsx";
import {
  GroupIcon,
  ListIcon,
  MenuIcon,
  SMART_ICONS,
  SettingsIcon,
  SyncIcon,
  TagIcon,
} from "./icons.jsx";

/**
 * A NavigationView, which is what this pane is in a Windows app.
 *
 * The conventions it follows, none of them decoration: the pane collapses to a
 * rail of icons from a hamburger sharing a row with the app name; the selected
 * item is marked by an accent bar at its leading edge rather than by a tinted
 * pill; icons are outlines in the text colour rather than white artwork on
 * coloured circles, with a list's own colour reduced to a dot; an item's count
 * is a filled accent badge rather than grey text; tags are items in the pane
 * rather than a cloud of chips; and Settings is a footer item here instead of
 * a glyph in a title bar.
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
      {/* An InfoBadge: a filled accent pill, not grey text. Zero is not news,
          so it is left off rather than drawn as an empty badge, and a list of
          1,219 open items is capped -- past a point the number stops being
          information and starts being a column of digits. */}
      {count ? (
        <span className="nav-badge" title={String(count)}>
          {Number(count) > 999 ? "999+" : count}
        </span>
      ) : null}
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
      <nav className="nav-items">
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

      <div className="nav-scroll">
        <div className="nav-group-label">My lists</div>
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

        {tags.length > 0 && (
          <>
            <div className="nav-group-label">Tags</div>
            <nav className="nav-items">
              {tags.map((t) =>
                item(`tag:${t.name}`, {
                  active: tag === t.name,
                  ref: tag === t.name ? activeRef : null,
                  onClick: () =>
                    onSelect(tag === t.name ? { scope: "today" } : { tag: t.name }),
                  icon: <TagIcon />,
                  name: `#${t.name}`,
                  count: t.n ?? "",
                })
              )}
            </nav>
          </>
        )}
      </div>

      <div className="nav-foot">
        {sync ? <SyncBar sync={sync} /> : null}
        {!sync && !collapsed && <div className="nav-status">{statusText}</div>}
        <button className="nav-item" onClick={onSync} title="Sync now">
          <span className="nav-pip" aria-hidden="true" />
          <span className={`nav-icon${sync ? " spinning" : ""}`}>
            <SyncIcon />
          </span>
          <span className="nav-name">Sync now</span>
        </button>
        <button className="nav-item" onClick={onSettings} title="Settings (Ctrl+,)">
          <span className="nav-pip" aria-hidden="true" />
          <span className="nav-icon"><SettingsIcon /></span>
          <span className="nav-name">Settings</span>
        </button>
      </div>
    </aside>
  );
}

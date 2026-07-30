import { forwardRef, useImperativeHandle, useMemo, useRef, useState } from "react";
import {
  SORTS,
  dayHeading,
  dueClass,
  formatDue,
  formatTime,
  priorityLabel,
  priorityMarks,
} from "../format.js";

/** Views that span more than one day read better broken up by date. */
const DATE_GROUPED = new Set(["upcoming", "all", "today"]);

function Row({ r, lists, showList, selected, onSelect, onToggle, onRestore, inTrash, dateOnly }) {
  return (
    <li
      className={`reminder${r.completed ? " done" : ""}${selected ? " selected" : ""}`}
      onClick={() => onSelect(r.id)}
    >
      {inTrash ? (
        <button
          className="restore-btn"
          title="Put back"
          onClick={(e) => {
            e.stopPropagation();
            onRestore(r);
          }}
        >
          ↺
        </button>
      ) : (
        <input
          type="checkbox"
          checked={!!r.completed}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onToggle(r, e.target.checked)}
        />
      )}

      <div className="reminder-main">
        <div className="reminder-title">{r.title || "(untitled)"}</div>
        <div className="reminder-meta">
          {priorityMarks(r.priority) && (
            <span className={`prio p${r.priority}`} title={priorityLabel(r.priority)}>
              {priorityMarks(r.priority)}
            </span>
          )}
          {r.due_date && (
            <span className={`due ${dueClass(r.due_date, r.completed)}`}>
              {/* Under a date heading the date is redundant; show the time. */}
              {dateOnly ? formatTime(r.due_date) : formatDue(r.due_date)}
            </span>
          )}
          {showList &&
            (() => {
              const l = lists.find((x) => x.id === r.list_id);
              return l ? (
                <span className="list-chip" style={{ color: l.color_hex || "" }}>
                  {l.title}
                </span>
              ) : null;
            })()}
          {(r.tags || []).map((t) => (
            <span key={t} className="pill tag">
              #{t}
            </span>
          ))}
          {r.dirty ? <span className="pending" title="Not yet synced to iCloud" /> : null}
        </div>
      </div>
    </li>
  );
}

const ListPane = forwardRef(function ListPane(
  {
    title, rows, lists, scope, listId, globalSearch,
    search, setSearch, searchScope, onToggleSearchScope,
    showDone, setShowDone, sortBy, setSortBy,
    selectedId, onSelect, onNew, onPrint, onToggleComplete, onRestore,
  },
  ref
) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const inputRef = useRef(null);

  useImperativeHandle(ref, () => ({
    open() {
      setSearchOpen(true);
      requestAnimationFrame(() => inputRef.current?.focus());
    },
  }));

  const inTrash = scope === "deleted";
  const showListChip = !listId || globalSearch;

  // Date headings only make sense when the rows are actually in date order.
  const grouped = useMemo(() => {
    const byDate =
      !search &&
      (sortBy === "manual" || sortBy === "due") &&
      DATE_GROUPED.has(scope);
    if (!byDate) return [{ key: "_all", label: null, items: rows }];

    const buckets = new Map();
    for (const r of rows) {
      const h = dayHeading(r.due_date);
      const b = buckets.get(h.key) || { ...h, items: [] };
      b.items.push(r);
      buckets.set(h.key, b);
    }
    return [...buckets.values()].sort((a, b) => a.order - b.order);
  }, [rows, search, sortBy, scope]);

  const showDates = grouped.length > 1 || grouped[0]?.label;

  return (
    <main className="list-pane">
      <header className="list-head">
        <div className="list-title-row">
          <h2 style={{ color: title.color || undefined }}>{title.text}</h2>

          <div className="head-actions">
            <div className={`search-wrap${searchOpen ? " open" : ""}`}>
              <button
                className="icon-btn round"
                title="Search (Ctrl+F)"
                onClick={() => {
                  setSearchOpen(true);
                  requestAnimationFrame(() => inputRef.current?.focus());
                }}
              >
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor"
                     strokeWidth="1.9" strokeLinecap="round" aria-hidden="true">
                  <circle cx="8.5" cy="8.5" r="5.5" />
                  <path d="M12.7 12.7 L17 17" />
                </svg>
              </button>
              <div className="search-box">
                <input
                  ref={inputRef}
                  type="search"
                  placeholder="Search…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      e.stopPropagation();
                      setSearch("");
                      setSearchOpen(false);
                    }
                  }}
                />
                <button
                  className={`scope-btn${searchScope === "global" ? " global" : ""}`}
                  title="Toggle search scope"
                  onClick={onToggleSearchScope}
                >
                  {searchScope === "global" ? "Everywhere" : "In List"}
                </button>
                <button
                  className="icon-btn"
                  title="Close search"
                  onClick={() => {
                    setSearch("");
                    setSearchOpen(false);
                  }}
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="menu-wrap">
              <button
                className="icon-btn round"
                title="Sort"
                onClick={() => setSortOpen((v) => !v)}
              >
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor"
                     strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                  <path d="M4 6h12M6 10h8M8 14h4" />
                </svg>
              </button>
              {sortOpen && (
                <>
                  <div className="menu-backdrop" onClick={() => setSortOpen(false)} />
                  <div className="menu" role="menu">
                    <div className="menu-label">Sort by</div>
                    {SORTS.map((s) => (
                      <button
                        key={s.value}
                        className={`menu-item${sortBy === s.value ? " on" : ""}`}
                        onClick={() => {
                          setSortBy(s.value);
                          setSortOpen(false);
                        }}
                      >
                        <span className="tick">{sortBy === s.value ? "✓" : ""}</span>
                        {s.label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            <button className="icon-btn round" title="Print (Ctrl+P)" onClick={onPrint}>
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor"
                   strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
                   aria-hidden="true">
                <path d="M6 7.5V3h8v4.5" />
                <path d="M6 14.5H4.5A1.5 1.5 0 0 1 3 13V9.5A1.5 1.5 0 0 1 4.5 8h11A1.5 1.5 0 0 1 17 9.5V13a1.5 1.5 0 0 1-1.5 1.5H14" />
                <rect x="6" y="12" width="8" height="5.5" rx="1" />
              </svg>
            </button>

            <button className="add-btn" title="New reminder (Ctrl+N)" onClick={onNew}>
              +
            </button>
          </div>
        </div>

        <div className="list-actions">
          {!inTrash && (
            <label className="check">
              <input
                type="checkbox"
                checked={showDone}
                onChange={(e) => setShowDone(e.target.checked)}
              />
              Show completed
            </label>
          )}
          <span className="hint tiny">
            {search
              ? `${rows.length} result${rows.length === 1 ? "" : "s"} ${
                  globalSearch ? "everywhere" : "in this list"
                }`
              : scope === "completed"
              ? "50 most recently completed"
              : ""}
          </span>
        </div>
      </header>

      {rows.length === 0 ? (
        <p className="empty">
          {search
            ? "No matches."
            : inTrash
            ? "Nothing deleted."
            : scope === "completed"
            ? "Nothing completed yet."
            : "Nothing here."}
        </p>
      ) : (
        <div className="reminders-scroll">
          {grouped.map((g) => (
            <section key={g.key} className="day-group">
              {showDates && g.label && (
                <div className="day-head">
                  <span className="day-label">{g.label}</span>
                  {g.sub && <span className="day-sub">{g.sub}</span>}
                  <span className="day-count">{g.items.length}</span>
                </div>
              )}
              <ul className="reminders">
                {g.items.map((r) => (
                  <Row
                    key={r.id}
                    r={r}
                    lists={lists}
                    showList={showListChip}
                    selected={selectedId === r.id}
                    onSelect={onSelect}
                    onToggle={onToggleComplete}
                    onRestore={onRestore}
                    inTrash={inTrash}
                    dateOnly={Boolean(showDates && g.label && g.key !== "none")}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </main>
  );
});

export default ListPane;

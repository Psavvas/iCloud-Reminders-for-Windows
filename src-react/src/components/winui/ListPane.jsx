import { forwardRef, useImperativeHandle, useMemo, useRef, useState } from "react";
import {
  SORTS,
  dayHeading,
  dueClass,
  formatDue,
  formatTime,
  priorityLabel,
} from "../../format.js";
import Composer from "../Composer.jsx";
import { AddIcon, PrintIcon, RestoreIcon, SearchIcon, SortIcon } from "./icons.jsx";

/** Views that span more than one day read better broken up by date. */
const DATE_GROUPED = new Set(["upcoming", "all", "today"]);

/** Windows shows priority as a word, not as Apple's run of exclamation marks. */
const PRIORITY_CLASS = { 1: "high", 5: "medium", 9: "low" };

function Row({
  r, lists, showList, selected, onSelect, onToggle, onRestore, inTrash,
  dateOnly, leaving,
}) {
  const prio = PRIORITY_CLASS[Number(r.priority)];
  return (
    <li
      className={
        "win-row" +
        (r.completed ? " done" : "") +
        (selected ? " selected" : "") +
        (leaving ? " leaving" : "")
      }
      onClick={() => onSelect(r.id)}
    >
      <span className="win-row-bar" aria-hidden="true" />
      {inTrash ? (
        <button
          className="win-restore"
          title="Put back"
          aria-label="Put back"
          onClick={(e) => {
            e.stopPropagation();
            onRestore(r);
          }}
        >
          <RestoreIcon />
        </button>
      ) : (
        <input
          type="checkbox"
          className="win-check"
          /* Ticks the moment it is clicked. The write and the reload behind it
             take longer than the eye allows for a checkbox. */
          checked={!!r.completed || leaving}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onToggle(r, e.target.checked)}
        />
      )}

      <div className="win-row-main">
        <div className="win-row-title">{r.title || "(untitled)"}</div>
        <div className="win-row-meta">
          {r.due_date && (
            <span className={`due ${dueClass(r.due_date, r.completed, r.all_day)}`}>
              {/* Under a dated heading the date is redundant; show the time. */}
              {dateOnly
                ? formatTime(r.due_date, r.all_day)
                : formatDue(r.due_date, r.all_day)}
            </span>
          )}
          {prio && (
            <span className={`win-badge prio-${prio}`}>
              {priorityLabel(r.priority)}
            </span>
          )}
          {showList &&
            (() => {
              const l = lists.find((x) => x.id === r.list_id);
              return l ? (
                <span className="win-badge list">
                  <span
                    className="win-badge-dot"
                    style={{ background: l.color_hex || "#8E8E93" }}
                  />
                  {l.title}
                </span>
              ) : null;
            })()}
          {(r.tags || []).map((t) => (
            <span key={t} className="win-badge tag">
              #{t}
            </span>
          ))}
          {r.dirty ? (
            <span className="win-pending" title="Not yet synced to iCloud" />
          ) : null}
        </div>
      </div>
    </li>
  );
}

const ListPane = forwardRef(function ListPane(
  {
    title, rows, lists, scope, listId, globalSearch, defaultListId,
    search, setSearch, searchScope, onToggleSearchScope,
    showDone, setShowDone, sortBy, setSortBy,
    selectedId, onSelect, onNew, onQuickCreate, onPrint, onToggleComplete, onRestore,
    viewKey, leaving,
  },
  ref
) {
  const [sortOpen, setSortOpen] = useState(false);
  const inputRef = useRef(null);
  const composerRef = useRef(null);

  useImperativeHandle(ref, () => ({
    // The search box is always on screen here -- there is nothing to open, so
    // Ctrl+F only has to put the caret in it.
    open() {
      inputRef.current?.focus();
      inputRef.current?.select();
    },
    compose() {
      composerRef.current?.focus();
    },
  }));

  const inTrash = scope === "deleted";
  const showListChip = !listId || globalSearch;

  // Nothing can be created into Deleted or Completed, and a composer sitting
  // under a set of search results would create somewhere you cannot see.
  const canCompose = !inTrash && scope !== "completed" && !search;
  const composerHint =
    scope === "today" ? "New Reminder, due today" : "New Reminder";

  const composer = canCompose ? (
    <ul className="win-rows">
      <Composer
        ref={composerRef}
        placeholder={composerHint}
        lists={lists}
        defaultListId={defaultListId}
        onCreate={onQuickCreate}
        onDetails={onNew}
      />
    </ul>
  ) : null;

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
    <main className="win-content">
      <header className="win-head">
        <h2 className="win-title">{title.text}</h2>

        {/* A CommandBar: labelled buttons in a row, not a cluster of bare
            glyphs. Windows names its commands. */}
        <div className="win-commands">
          <button
            className="win-cmd"
            onClick={() => (canCompose ? composerRef.current?.focus() : onNew(null))}
            title="New reminder (Ctrl+N)"
          >
            <AddIcon />
            <span>New</span>
          </button>

          <div className="menu-wrap">
            <button
              className={`win-cmd${sortOpen ? " on" : ""}`}
              onClick={() => setSortOpen((v) => !v)}
              title="Sort"
              aria-haspopup="menu"
              aria-expanded={sortOpen}
            >
              <SortIcon />
              <span>Sort</span>
            </button>
            {sortOpen && (
              <>
                <div className="menu-backdrop" onClick={() => setSortOpen(false)} />
                <div className="menu" role="menu">
                  {SORTS.map((s) => (
                    <button
                      key={s.value}
                      className={`menu-item${sortBy === s.value ? " on" : ""}`}
                      role="menuitemradio"
                      aria-checked={sortBy === s.value}
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

          <button className="win-cmd" onClick={onPrint} title="Print (Ctrl+P)">
            <PrintIcon />
            <span>Print</span>
          </button>

          <span className="win-cmd-gap" />

          {!inTrash && (
            <label className="win-checkbox">
              <input
                type="checkbox"
                checked={showDone}
                onChange={(e) => setShowDone(e.target.checked)}
              />
              <span>Show completed</span>
            </label>
          )}

          {/* An AutoSuggestBox, always on screen. Windows does not hide search
              behind a magnifier that expands. */}
          <div className="win-search">
            <span className="win-search-icon"><SearchIcon /></span>
            <input
              ref={inputRef}
              type="search"
              placeholder="Search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.stopPropagation();
                  setSearch("");
                }
              }}
            />
            <button
              className={`win-scope${searchScope === "global" ? " global" : ""}`}
              title="Search this list, or everywhere"
              onClick={onToggleSearchScope}
            >
              {searchScope === "global" ? "All" : "List"}
            </button>
          </div>
        </div>

        <div className="win-subhead">
          {search
            ? `${rows.length} result${rows.length === 1 ? "" : "s"} ${
                globalSearch ? "everywhere" : "in this list"
              }`
            : scope === "completed"
            ? "50 most recently completed"
            : ""}
        </div>
      </header>

      {/* Re-keyed per view: the rows are all replaced on a switch anyway, and
          remounting both replays the enter animation and puts the new list at
          the top rather than inheriting the last one's scroll. */}
      <div className="win-scroll" key={viewKey}>
        {rows.length === 0 ? (
          <p className="win-empty">
            {search
              ? "No matches."
              : inTrash
              ? "Nothing deleted."
              : scope === "completed"
              ? "Nothing completed yet."
              : "Nothing here."}
          </p>
        ) : (
          grouped.map((g) => (
            <section key={g.key} className="win-group">
              {showDates && g.label && (
                <div className="win-group-head">
                  <span className="win-group-label">{g.label}</span>
                  {g.sub && <span className="win-group-sub">{g.sub}</span>}
                  <span className="win-group-count">{g.items.length}</span>
                </div>
              )}
              <ul className="win-rows">
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
                    leaving={leaving?.has(r.id)}
                    // Only hide the date when the heading above actually states
                    // one. "Overdue" and "No Date" span many days, so a bare
                    // time there reads as today and is misleading.
                    dateOnly={Boolean(
                      showDates && g.label && g.key !== "none" && g.key !== "overdue"
                    )}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
        {/* Last, so it sits where the reminder it creates will appear rather
            than above everything the list already has. */}
        {composer}
      </div>
    </main>
  );
});

export default ListPane;

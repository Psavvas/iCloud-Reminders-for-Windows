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
import { AddIcon, MoreIcon, RestoreIcon, SearchIcon, SortIcon } from "./icons.jsx";

/** Views that span more than one day read better broken up by date. */
const DATE_GROUPED = new Set(["upcoming", "all", "today"]);

/**
 * A ListViewItem.
 *
 * Three lines at most: the title, a subtitle of everything that qualifies it,
 * and the note. The subtitle is plain text joined by middots rather than a run
 * of coloured badges -- Windows writes "High priority" and lets the type do the
 * work, where Apple would draw three exclamation marks in red.
 */
function Row({
  r, lists, showList, selected, onSelect, onToggle, onRestore, inTrash,
  dateOnly, leaving,
}) {
  const bits = [];
  if (r.due_date) {
    bits.push({
      key: "due",
      // Under a dated heading the date is redundant; show the time.
      text: dateOnly ? formatTime(r.due_date, r.all_day) : formatDue(r.due_date, r.all_day),
      className: `due ${dueClass(r.due_date, r.completed, r.all_day)}`,
    });
  }
  if (Number(r.priority)) {
    bits.push({ key: "prio", text: `${priorityLabel(r.priority)} priority` });
  }
  if (showList) {
    const l = lists.find((x) => x.id === r.list_id);
    if (l) bits.push({ key: "list", text: l.title });
  }
  for (const t of r.tags || []) {
    bits.push({ key: `t-${t}`, text: `#${t}`, className: "tag" });
  }

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
        <div className="win-row-title">
          {r.title || "(untitled)"}
          {r.flagged ? (
            <span className="win-flag" title="Flagged" aria-label="Flagged" />
          ) : null}
        </div>
        {/* Boolean(), not the raw value. `dirty` arrives from SQLite as 0 or 1,
            and `{0 && <div/>}` renders a literal 0 into the row. */}
        {(bits.length > 0 || Boolean(r.dirty)) && (
          <div className="win-row-sub">
            {bits.map((b, i) => (
              <span key={b.key}>
                {i > 0 && <span className="win-dot-sep">·</span>}
                <span className={b.className}>{b.text}</span>
              </span>
            ))}
            {r.dirty ? (
              <span>
                {bits.length > 0 && <span className="win-dot-sep">·</span>}
                <span className="win-pending-text">Waiting to sync</span>
              </span>
            ) : null}
          </div>
        )}
        {r.description ? <div className="win-row-note">{r.description}</div> : null}
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
  const [menu, setMenu] = useState(null); // sort | more
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
  const closeMenu = () => setMenu(null);

  return (
    <main className="win-content">
      <header className="win-head">
        <div className="win-title-row">
          <h1 className="win-title">{title.text}</h1>
          {/* The one command that gets a button of its own, and it is the
              accent one. Everything else is an icon or lives in the overflow,
              which is how a Windows page header ranks its commands. */}
          <button
            className="win-primary"
            title="New reminder (Ctrl+N)"
            onClick={() => (canCompose ? composerRef.current?.focus() : onNew(null))}
          >
            <AddIcon />
            <span>Add reminder</span>
          </button>
        </div>

        <div className="win-toolbar">
          {/* An AutoSuggestBox: always on screen, its glyph inside the field on
              the trailing edge. Windows does not fold search behind a button
              that expands. */}
          <div className="win-search">
            <input
              ref={inputRef}
              type="search"
              placeholder="Search reminders"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.stopPropagation();
                  setSearch("");
                }
              }}
            />
            <span className="win-search-icon" aria-hidden="true">
              <SearchIcon />
            </span>
          </div>

          <div className="menu-wrap">
            <button
              className={`win-icon${menu === "sort" ? " on" : ""}`}
              onClick={() => setMenu(menu === "sort" ? null : "sort")}
              title="Sort"
              aria-label="Sort"
              aria-haspopup="menu"
              aria-expanded={menu === "sort"}
            >
              <SortIcon />
            </button>
            {menu === "sort" && (
              <>
                <div className="menu-backdrop" onClick={closeMenu} />
                <div className="menu" role="menu">
                  <div className="menu-label">Sort by</div>
                  {SORTS.map((s) => (
                    <button
                      key={s.value}
                      className={`menu-item${sortBy === s.value ? " on" : ""}`}
                      role="menuitemradio"
                      aria-checked={sortBy === s.value}
                      onClick={() => {
                        setSortBy(s.value);
                        closeMenu();
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

          <div className="menu-wrap">
            <button
              className={`win-icon${menu === "more" ? " on" : ""}`}
              onClick={() => setMenu(menu === "more" ? null : "more")}
              title="More"
              aria-label="More options"
              aria-haspopup="menu"
              aria-expanded={menu === "more"}
            >
              <MoreIcon />
            </button>
            {menu === "more" && (
              <>
                <div className="menu-backdrop" onClick={closeMenu} />
                <div className="menu" role="menu">
                  <button
                    className="menu-item"
                    role="menuitem"
                    onClick={() => {
                      closeMenu();
                      onPrint();
                    }}
                  >
                    <span className="tick" />
                    Print…
                  </button>
                  <button
                    className="menu-item"
                    role="menuitemcheckbox"
                    aria-checked={searchScope === "global"}
                    onClick={() => {
                      onToggleSearchScope();
                      closeMenu();
                    }}
                  >
                    <span className="tick">{searchScope === "global" ? "✓" : ""}</span>
                    Search everywhere
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {search ? (
          <div className="win-subhead">
            {rows.length} result{rows.length === 1 ? "" : "s"}{" "}
            {globalSearch ? "everywhere" : "in this list"}
          </div>
        ) : null}
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

      {/* A status bar. The filter that changes what the list contains belongs
          next to the count of what it contains, not up among the commands. */}
      <footer className="win-statusbar">
        {!inTrash ? (
          <label className="win-switch inline">
            <input
              type="checkbox"
              checked={showDone}
              onChange={(e) => setShowDone(e.target.checked)}
            />
            <span className="win-switch-track" aria-hidden="true" />
            <span className="win-switch-text">Show completed</span>
          </label>
        ) : (
          <span />
        )}
        <span className="win-count">
          {scope === "completed"
            ? "50 most recently completed"
            : `${rows.length} reminder${rows.length === 1 ? "" : "s"}`}
        </span>
      </footer>
    </main>
  );
});

export default ListPane;

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  PRIORITIES,
  TIME_PRESETS,
  datePresets,
  dueLabel,
  formatClock,
  priorityLabel,
} from "../format.js";
import {
  CalendarIcon,
  ClockIcon,
  FlagIcon,
  InfoIcon,
  ListsIcon,
} from "./icons.jsx";

/**
 * Type a reminder where the reminder will be, instead of in a dialog.
 *
 * Apple's model, and the reason it is worth copying: the row being typed in
 * *is* the reminder, and everything a reminder usually needs -- a note, a date,
 * a time, a priority, a list -- is one press away underneath it rather than
 * behind a modal. Enter commits and leaves the card open, so the next one is
 * already being typed.
 *
 * Three states, in one component because they are one gesture:
 *
 *   collapsed   a single empty row, the same height as a reminder
 *   expanded    the row lifts into a card: title, note, and the action row
 *   menu open   one popover at a time, anchored to the button that opened it
 *
 * The action row is deliberately not a form. A button with nothing set is a
 * plain circle; setting a value turns it into a filled pill stating the value,
 * so the card reads back what it is about to create without a summary line.
 *
 * There is no tag button, unlike Apple's. Tags cannot be written through this
 * API at all -- see the README -- and a control that quietly does nothing is
 * worse than one that is not there.
 *
 * Both interfaces use this component. The gesture is the same in each; what
 * the two stylesheets disagree about is the shape of the card and its buttons.
 */

const EMPTY = { title: "", note: "", date: "", time: "", priority: 0 };

/** Roughly how tall the tallest of these menus gets, for the flip decision. */
const MENU_HEIGHT = 290;
const MENU_WIDTH = 296;

/**
 * A popover anchored to the button that opened it.
 *
 * Positioned against the viewport rather than inside the button's own box: the
 * card lives in the scrolling list, and an absolutely positioned child of a
 * scroller is clipped by it -- which is exactly where a menu of five dates
 * would end up, given the composer sits at the bottom of the list. So the
 * button is measured on open, and the menu opens upward when there is not
 * enough room below.
 */
function Menu({ anchorRef, label, onClose, children }) {
  const [pos, setPos] = useState(null);

  useLayoutEffect(() => {
    const r = anchorRef.current?.getBoundingClientRect();
    if (!r) return;
    const below = window.innerHeight - r.bottom;
    const up = below < MENU_HEIGHT && r.top > below;
    setPos({
      up,
      // Clamped, so a menu opened by the rightmost button stays on screen.
      left: Math.max(8, Math.min(r.left, window.innerWidth - MENU_WIDTH)),
      top: up ? undefined : r.bottom + 6,
      bottom: up ? window.innerHeight - r.top + 6 : undefined,
    });
  }, [anchorRef]);

  return (
    <>
      <div className="menu-backdrop" onPointerDown={onClose} />
      <div
        className={`menu composer-menu${pos && pos.up ? " upward" : ""}`}
        role="menu"
        aria-label={label}
        style={
          pos
            ? { left: pos.left, top: pos.top, bottom: pos.bottom }
            : // Hidden for the one frame before the measurement lands, so it is
              // never seen in the wrong place first.
              { visibility: "hidden", top: 0, left: 0 }
        }
      >
        {children}
      </div>
    </>
  );
}

function MenuItem({ on, icon, children, onClick }) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={!!on}
      className={`menu-item${on ? " on" : ""}`}
      onClick={onClick}
    >
      <span className="tick">{on ? "✓" : ""}</span>
      {icon ? <span className="menu-icon">{icon}</span> : null}
      {children}
    </button>
  );
}

/** A quick-action button: bare circle when unset, filled pill once it has one. */
function Tool({ icon, label, value, title, open, onToggle, onClose, children }) {
  const btnRef = useRef(null);
  return (
    <div className="tool-wrap">
      <button
        ref={btnRef}
        type="button"
        className={`tool${value ? " set" : ""}`}
        title={title}
        aria-label={value ? `${title}: ${label}` : title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className="tool-icon">{icon}</span>
        {value ? <span className="tool-label">{label}</span> : null}
      </button>
      {open && (
        <Menu anchorRef={btnRef} label={title} onClose={onClose}>
          {children}
        </Menu>
      )}
    </div>
  );
}

const Composer = forwardRef(function Composer(
  { placeholder, lists, defaultListId, onCreate, onDetails },
  ref
) {
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(null); // date | time | priority | list
  const [draft, setDraft] = useState({ ...EMPTY, listId: defaultListId || "" });

  const rootRef = useRef(null);
  const titleRef = useRef(null);
  const collapsedRef = useRef(null);

  const usable = useMemo(
    () => (lists || []).filter((l) => !l.is_group),
    [lists]
  );

  // The view moved, or Settings changed where new reminders go. Nothing typed
  // yet, so following it is right; mid-draft it would move the reminder out
  // from under someone who had already picked a list.
  useEffect(() => {
    setDraft((d) => (d.title || d.note ? d : { ...d, listId: defaultListId || "" }));
  }, [defaultListId]);

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));
  const closeMenu = () => setMenu(null);

  const expand = useCallback(() => {
    setOpen(true);
    requestAnimationFrame(() => titleRef.current?.focus());
  }, []);

  const collapse = useCallback(() => {
    setOpen(false);
    setMenu(null);
    setDraft((d) => ({ ...EMPTY, listId: d.listId }));
  }, []);

  useImperativeHandle(ref, () => ({
    focus() {
      if (open) titleRef.current?.focus();
      else expand();
    },
  }));

  /** What the card would create, in the shape the sidecar takes. */
  const buildPayload = useCallback(() => {
    const p = {
      list_id: draft.listId || defaultListId || (usable[0] || {}).id,
      title: draft.title.trim(),
      description: draft.note.trim(),
      priority: Number(draft.priority) || 0,
    };
    if (draft.date) {
      // No time means a date and no time -- all-day, the way Apple stores it --
      // rather than midnight, which reads as "no time" and fires at 00:00.
      p.due_date = draft.time ? `${draft.date}T${draft.time}:00` : draft.date;
      p.all_day = !draft.time;
    }
    return p;
  }, [draft, defaultListId, usable]);

  const commit = useCallback(() => {
    if (!draft.title.trim()) return;
    onCreate(buildPayload());
    // The list is the one thing worth keeping: whoever just filed something
    // here is likely filing the next one here too.
    setDraft((d) => ({ ...EMPTY, listId: d.listId }));
    setMenu(null);
    requestAnimationFrame(() => titleRef.current?.focus());
  }, [draft.title, onCreate, buildPayload]);

  // Clicking away is a commit, not a cancel -- text typed into the list has
  // already been "written down" as far as anyone is concerned. Anything inside
  // the card is not away, and neither is a menu, which renders outside it.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      const root = rootRef.current;
      if (!root || root.contains(e.target)) return;
      // A menu, and the backdrop that dismisses it, both render outside the
      // card. Neither is "away" -- a click on the backdrop closes the menu and
      // must leave the half-typed reminder alone.
      if (e.target.closest && e.target.closest(".composer-menu, .menu-backdrop")) {
        return;
      }
      commit();
      collapse();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [open, commit, collapse]);

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      // Stop this reaching the window handler, which closes sheets.
      e.stopPropagation();
      if (menu) setMenu(null);
      else collapse();
    }
  };

  const openDetails = () => {
    const d = { ...draft, listId: draft.listId || defaultListId || "" };
    collapse();
    onDetails(d);
  };

  if (!open) {
    return (
      <li className="reminder composer" onClick={expand}>
        {/* Not a real checkbox -- there is nothing to complete yet. It exists so
            the row lines up with the reminders above it rather than jumping. */}
        <span className="composer-dot" aria-hidden="true" />
        <input
          ref={collapsedRef}
          className="composer-input"
          type="text"
          value={draft.title}
          placeholder={placeholder}
          aria-label="New reminder"
          autoComplete="off"
          onFocus={expand}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              e.currentTarget.blur();
            }
          }}
          /* Focus normally arrives first and has already expanded this away.
             This is the case where it does not -- a paste or an IME commit
             into a row that was clicked but not focused -- and dropping that
             text on the floor would be the same bug as a lost keystroke. */
          onChange={(e) => {
            const v = e.target.value;
            set("title", v);
            setOpen(true);
            requestAnimationFrame(() => {
              const el = titleRef.current;
              el?.focus();
              el?.setSelectionRange(v.length, v.length);
            });
          }}
        />
      </li>
    );
  }

  const presets = datePresets();
  const chosenList = usable.find((l) => l.id === draft.listId) || usable[0];

  return (
    <li className="composer-card" ref={rootRef}>
      <div className="cc-head">
        <span className="composer-dot" aria-hidden="true" />
        <div className="cc-fields">
          <input
            ref={titleRef}
            className="cc-title"
            type="text"
            value={draft.title}
            placeholder={placeholder}
            aria-label="New reminder"
            autoComplete="off"
            onChange={(e) => set("title", e.target.value)}
            onKeyDown={onKeyDown}
          />
          <input
            className="cc-note"
            type="text"
            value={draft.note}
            placeholder="Add Note"
            aria-label="Note"
            autoComplete="off"
            onChange={(e) => set("note", e.target.value)}
            onKeyDown={onKeyDown}
          />
        </div>
        <button
          type="button"
          className="cc-info"
          title="More options"
          aria-label="More options"
          onClick={openDetails}
        >
          <InfoIcon />
        </button>
      </div>

      <div className="cc-tools">
        <Tool
          icon={<CalendarIcon />}
          title="Due date"
          value={draft.date}
          label={dueLabel(draft.date)}
          open={menu === "date"}
          onToggle={() => setMenu(menu === "date" ? null : "date")}
          onClose={closeMenu}
        >
          <MenuItem
            on={!draft.date}
            onClick={() => {
              // A time without a date is not a due date at all.
              setDraft((d) => ({ ...d, date: "", time: "" }));
              closeMenu();
            }}
          >
            None
          </MenuItem>
          <div className="menu-sep" />
          {presets.map((p) => (
            <MenuItem
              key={p.key}
              on={draft.date === p.value}
              icon={<CalendarIcon day={p.day} />}
              onClick={() => {
                set("date", p.value);
                closeMenu();
              }}
            >
              {p.label}
            </MenuItem>
          ))}
          <div className="menu-sep" />
          <label className="menu-field">
            <span>Custom</span>
            <input
              type="date"
              value={draft.date}
              onChange={(e) => {
                set("date", e.target.value);
                if (e.target.value) closeMenu();
              }}
            />
          </label>
        </Tool>

        {/* A time is meaningless without a day to hang it on, so it only
            appears once there is one -- the order Apple reveals it in too. */}
        {draft.date && (
          <Tool
            icon={<ClockIcon />}
            title="Time"
            value={draft.time}
            label={formatClock(draft.time)}
            open={menu === "time"}
            onToggle={() => setMenu(menu === "time" ? null : "time")}
            onClose={closeMenu}
          >
            <MenuItem
              on={!draft.time}
              onClick={() => {
                set("time", "");
                closeMenu();
              }}
            >
              All Day
            </MenuItem>
            <div className="menu-sep" />
            {TIME_PRESETS.map((t) => (
              <MenuItem
                key={t}
                on={draft.time === t}
                onClick={() => {
                  set("time", t);
                  closeMenu();
                }}
              >
                {formatClock(t)}
              </MenuItem>
            ))}
            <div className="menu-sep" />
            <label className="menu-field">
              <span>Custom</span>
              <input
                type="time"
                value={draft.time}
                onChange={(e) => set("time", e.target.value)}
              />
            </label>
          </Tool>
        )}

        <Tool
          icon={<FlagIcon />}
          title="Priority"
          value={draft.priority}
          label={priorityLabel(draft.priority)}
          open={menu === "priority"}
          onToggle={() => setMenu(menu === "priority" ? null : "priority")}
          onClose={closeMenu}
        >
          {PRIORITIES.map((p) => (
            <MenuItem
              key={p.value}
              on={Number(draft.priority) === p.value}
              onClick={() => {
                set("priority", p.value);
                closeMenu();
              }}
            >
              {p.label}
            </MenuItem>
          ))}
        </Tool>

        {/* Always a pill, never a bare circle: everything lands in *some* list,
            and which one is the question a smart view cannot answer. */}
        <Tool
          icon={<ListsIcon />}
          title="List"
          value={chosenList ? chosenList.id : ""}
          label={chosenList ? chosenList.title : ""}
          open={menu === "list"}
          onToggle={() => setMenu(menu === "list" ? null : "list")}
          onClose={closeMenu}
        >
          <div className="menu-scroll">
            {usable.map((l) => (
              <MenuItem
                key={l.id}
                on={chosenList && chosenList.id === l.id}
                icon={
                  <span
                    className="menu-swatch"
                    style={{ background: l.color_hex || "#8E8E93" }}
                  />
                }
                onClick={() => {
                  set("listId", l.id);
                  closeMenu();
                }}
              >
                {l.title}
              </MenuItem>
            ))}
          </div>
        </Tool>

        <span className="cc-spacer" />
        <button
          type="button"
          className="cc-add"
          disabled={!draft.title.trim()}
          onClick={commit}
        >
          Add
        </button>
      </div>
    </li>
  );
});

export default Composer;

import { forwardRef, useImperativeHandle, useRef, useState } from "react";

/**
 * Type a reminder where the reminder will be, instead of in a dialog.
 *
 * Apple's model, and the reason it is worth copying: adding three things is one
 * gesture, not three round trips through a modal. Enter commits and leaves the
 * caret here, so the next one is already being typed.
 *
 * Anything beyond a title -- a list, a priority, a date on a reminder that is
 * not going in Today -- is a detour, so it lives behind Details, which hands
 * the typed title to the full sheet rather than throwing it away.
 */
const Composer = forwardRef(function Composer({ placeholder, onCreate, onDetails }, ref) {
  const [title, setTitle] = useState("");
  const inputRef = useRef(null);

  useImperativeHandle(ref, () => ({
    focus() {
      inputRef.current?.focus();
    },
  }));

  const commit = () => {
    const t = title.trim();
    if (!t) return;
    setTitle("");
    // Deliberately not awaited: the caret should be free for the next reminder
    // before the write finishes, which is the whole point of typing in place.
    onCreate(t);
  };

  return (
    <li className="reminder composer">
      {/* Not a real checkbox -- there is nothing to complete yet. It exists so
          the row lines up with the reminders above it rather than jumping. */}
      <span className="composer-dot" aria-hidden="true" />
      <input
        ref={inputRef}
        className="composer-input"
        type="text"
        value={title}
        placeholder={placeholder}
        aria-label="New reminder"
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            // Stop this reaching the window handler, which closes sheets.
            e.stopPropagation();
            setTitle("");
            inputRef.current?.blur();
          }
        }}
      />
      {title.trim() && (
        <button
          type="button"
          className="ghost composer-details"
          // onMouseDown, not onClick: onClick lands after blur has already
          // cleared the field, so the sheet would open empty.
          onMouseDown={(e) => {
            e.preventDefault();
            const t = title.trim();
            setTitle("");
            onDetails(t);
          }}
        >
          Details
        </button>
      )}
    </li>
  );
});

export default Composer;

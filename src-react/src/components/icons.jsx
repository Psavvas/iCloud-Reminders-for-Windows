/**
 * Sidebar glyphs.
 *
 * These were Unicode characters -- U+25C9, U+25A4, U+2261 and friends. Which
 * shape you actually got depended on which installed font first claimed the
 * codepoint, they carry their own metrics so they never centred properly in
 * their circle, and none of them look like anything Apple ships. Drawn as SVG
 * they are one weight, one grid, and the same at every scale factor.
 *
 * All of them are white-on-colour, so they are drawn in currentColor at a
 * stroke weight that survives being 15px across.
 */

const box = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2.1,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

/**
 * Today: a calendar showing today's date, the way Apple's does. The number is
 * the only thing distinguishing it from Scheduled, so it is worth the fiddle of
 * fitting real text into 24 units.
 */
export function TodayIcon() {
  return (
    <svg {...box}>
      {/* The header rule sits a clear 6 units below the top edge. Closer and
          the two strokes antialias into one solid band at 15px. */}
      <rect x="3" y="4.2" width="18" height="17.2" rx="4" />
      <path d="M3 10.4h18" />
      <text
        x="12"
        y="18.6"
        textAnchor="middle"
        fontSize="8.6"
        fontWeight="700"
        fill="currentColor"
        stroke="none"
        /* The app's stack is tracked tight; digits this small need it back. */
        letterSpacing="0"
      >
        {new Date().getDate()}
      </text>
    </svg>
  );
}

/** Scheduled: the same calendar with its binding rings, and no date. */
export function UpcomingIcon() {
  return (
    <svg {...box}>
      <rect x="3" y="5.4" width="18" height="15.8" rx="4" />
      <path d="M3 10.4h18M8 2.8v4.2M16 2.8v4.2" />
    </svg>
  );
}

/** All: an inbox tray, holding everything that has not been filed. */
export function AllIcon() {
  return (
    <svg {...box}>
      <path d="M3.2 14h4.1l1.5 2.6h6.4l1.5-2.6h4.1" />
      <path d="M3.2 14 6 5.9A2 2 0 0 1 7.9 4.5h8.2A2 2 0 0 1 18 5.9L20.8 14v3.4a2.1 2.1 0 0 1-2.1 2.1H5.3a2.1 2.1 0 0 1-2.1-2.1z" />
    </svg>
  );
}

export function CompletedIcon() {
  return (
    <svg {...box} strokeWidth={2.6}>
      <path d="M5 12.4 9.8 17.2 19 6.8" />
    </svg>
  );
}

export function DeletedIcon() {
  return (
    <svg {...box}>
      <path d="M4.2 6.6h15.6" />
      <path d="M9.6 6.6V5.1a1.4 1.4 0 0 1 1.4-1.4h2a1.4 1.4 0 0 1 1.4 1.4v1.5" />
      <path d="M6.6 6.6l.9 12.6a2 2 0 0 0 2 1.9h5a2 2 0 0 0 2-1.9l.9-12.6" />
      <path d="M10.4 10.6v6.4M13.6 10.6v6.4" />
    </svg>
  );
}

/**
 * A user list: bars of uneven length, so it reads as written items rather than
 * as a menu. Bullets would be white-on-colour at 3px and just muddy it.
 */
export function ListIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="5" y="6.1" width="14" height="2.6" rx="1.3" />
      <rect x="5" y="10.7" width="14" height="2.6" rx="1.3" />
      <rect x="5" y="15.3" width="9.4" height="2.6" rx="1.3" />
    </svg>
  );
}

/** A group holds lists rather than reminders, so it gets a folder. */
export function GroupIcon() {
  return (
    <svg {...box}>
      <path d="M3.4 7.6a2.1 2.1 0 0 1 2.1-2.1h3.6l2.1 2.6h7.3a2.1 2.1 0 0 1 2.1 2.1v7.9a2.1 2.1 0 0 1-2.1 2.1H5.5a2.1 2.1 0 0 1-2.1-2.1z" />
    </svg>
  );
}

export const SMART_ICONS = {
  today: TodayIcon,
  upcoming: UpcomingIcon,
  all: AllIcon,
  completed: CompletedIcon,
  deleted: DeletedIcon,
};

/* --------------------------------------------------------- composer glyphs */
/*
 * The quick-action row under a reminder being typed. Both interfaces use these
 * same drawings: what separates the two skins is the shape and colour of the
 * button around them, not the symbol inside it. Drawn on the same 24-unit grid
 * as everything above, at a lighter weight -- these sit at 16px on a plain
 * surface rather than 15px white-on-colour.
 */

const line = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

/** A calendar, optionally showing the day it would set -- as Apple's menu does. */
export function CalendarIcon({ day }) {
  return (
    <svg {...line}>
      <rect x="3.2" y="5.2" width="17.6" height="15.6" rx="3.2" />
      <path d="M3.2 9.8h17.6M8 3.2v3.6M16 3.2v3.6" />
      {day != null && (
        <text
          x="12"
          y="17.6"
          textAnchor="middle"
          fontSize="7.4"
          fontWeight="700"
          fill="currentColor"
          stroke="none"
          letterSpacing="0"
        >
          {day}
        </text>
      )}
    </svg>
  );
}

export function ClockIcon() {
  return (
    <svg {...line}>
      <circle cx="12" cy="12" r="8.6" />
      <path d="M12 7.2V12l3.2 2" />
    </svg>
  );
}

/** Priority. A flag rather than exclamation marks -- marks need a value to read. */
export function FlagIcon() {
  return (
    <svg {...line}>
      <path d="M6 21V4.4" />
      <path d="M6 5.1h9.8l-1.8 3.4 1.8 3.4H6" />
    </svg>
  );
}

/** Which list it lands in. */
export function ListsIcon() {
  return (
    <svg {...line}>
      <path d="M9 6.6h11M9 12h11M9 17.4h11" />
      <path d="M4.4 6.6h.01M4.4 12h.01M4.4 17.4h.01" strokeWidth="2.4" />
    </svg>
  );
}

/** Everything else, in the full sheet. */
export function InfoIcon() {
  return (
    <svg {...line}>
      <circle cx="12" cy="12" r="8.6" />
      <path d="M12 11v5.4" />
      <path d="M12 7.7h.01" strokeWidth="2.4" />
    </svg>
  );
}

/* ------------------------------------------------------- title-bar glyphs */
/*
 * These three were U+27F3, U+2699 and U+21BA, and were left behind when the
 * sidebar's own glyphs were redrawn. They have the same problem the others
 * did: which shape you get depends on which installed font first claims the
 * codepoint, and U+2699 in particular arrives as a colour emoji on Windows,
 * where every other control in the row is a monochrome stroke.
 */

export function SyncIcon() {
  return (
    <svg {...line} strokeWidth={1.9}>
      <path d="M20 12a8 8 0 1 1-2.34-5.66" />
      <path d="M20.4 3.4v4.2h-4.2" />
    </svg>
  );
}

export function SettingsIcon() {
  return (
    <svg {...line} strokeWidth={1.7}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.6l.6 2.4a7.2 7.2 0 0 1 2.1.87l2.1-1.3 2.32 2.32-1.3 2.1c.38.65.68 1.36.87 2.1l2.4.6v3.28l-2.4.6a7.2 7.2 0 0 1-.87 2.1l1.3 2.1-2.32 2.32-2.1-1.3a7.2 7.2 0 0 1-2.1.87l-.6 2.4h-3.28l-.6-2.4a7.2 7.2 0 0 1-2.1-.87l-2.1 1.3L2.6 17.9l1.3-2.1a7.2 7.2 0 0 1-.87-2.1l-2.4-.6v-3.28l2.4-.6c.19-.74.49-1.45.87-2.1L2.6 5.1l2.32-2.32 2.1 1.3a7.2 7.2 0 0 1 2.1-.87l.6-2.4z" />
    </svg>
  );
}

/** Put a deleted reminder back: an anticlockwise arrow, the undo direction. */
export function RestoreIcon() {
  return (
    <svg {...line} strokeWidth={1.9}>
      <path d="M4 12a8 8 0 1 0 2.34-5.66" />
      <path d="M3.6 3.4v4.2h4.2" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg {...line} strokeWidth={1.9}>
      <path d="M6.4 6.4l11.2 11.2M17.6 6.4L6.4 17.6" />
    </svg>
  );
}

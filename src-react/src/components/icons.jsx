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

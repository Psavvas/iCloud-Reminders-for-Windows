/**
 * Fluent-style glyphs for the Windows interface.
 *
 * Deliberately not the Apple set with different colours. Windows draws its
 * icons on a 16-unit grid at a single light stroke weight, outline-only, with
 * square-ish terminals and no filled shapes -- which is why these are separate
 * drawings rather than a restyle of icons.jsx.
 *
 * They are also drawn rather than taken from Segoe Fluent Icons: that font
 * ships with Windows 11 but not with Windows 10, where the codepoints fall
 * back to Segoe MDL2 Assets and land on different pictures. Drawing them means
 * both versions of Windows get the same app.
 */

const g = {
  viewBox: "0 0 20 20",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.3,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

/** Today: a calendar carrying the date, the only thing that separates it from
    Upcoming at 16px. */
export function TodayIcon() {
  return (
    <svg {...g}>
      <rect x="2.6" y="3.6" width="14.8" height="13.8" rx="2" />
      <path d="M2.6 7.4h14.8" />
      <text
        x="10"
        y="14.6"
        textAnchor="middle"
        fontSize="6.4"
        fontWeight="600"
        fill="currentColor"
        stroke="none"
        letterSpacing="0"
      >
        {new Date().getDate()}
      </text>
    </svg>
  );
}

export function UpcomingIcon() {
  return (
    <svg {...g}>
      <rect x="2.6" y="3.6" width="14.8" height="13.8" rx="2" />
      <path d="M2.6 7.4h14.8M6.6 2v3.2M13.4 2v3.2" />
    </svg>
  );
}

export function AllIcon() {
  return (
    <svg {...g}>
      <path d="M2.6 11.6h3.4l1.2 2h5.6l1.2-2h3.4" />
      <path d="M2.6 11.6 4.9 4.9a1.6 1.6 0 0 1 1.5-1.1h7.2a1.6 1.6 0 0 1 1.5 1.1l2.3 6.7v3.6a1.8 1.8 0 0 1-1.8 1.8H4.4a1.8 1.8 0 0 1-1.8-1.8z" />
    </svg>
  );
}

export function CompletedIcon() {
  return (
    <svg {...g}>
      <circle cx="10" cy="10" r="7.4" />
      <path d="M6.7 10.2 9 12.5l4.3-4.9" />
    </svg>
  );
}

export function DeletedIcon() {
  return (
    <svg {...g}>
      <path d="M3.4 5.4h13.2" />
      <path d="M8 5.4V4.2a1.2 1.2 0 0 1 1.2-1.2h1.6A1.2 1.2 0 0 1 12 4.2v1.2" />
      <path d="M5.4 5.4l.7 10.4a1.7 1.7 0 0 0 1.7 1.6h4.4a1.7 1.7 0 0 0 1.7-1.6l.7-10.4" />
      <path d="M8.5 8.6v5.4M11.5 8.6v5.4" />
    </svg>
  );
}

export function ListIcon() {
  return (
    <svg {...g}>
      <path d="M7.4 5.4h9.2M7.4 10h9.2M7.4 14.6h9.2" />
      <path d="M3.6 5.4h.01M3.6 10h.01M3.6 14.6h.01" strokeWidth="2" />
    </svg>
  );
}

export function GroupIcon() {
  return (
    <svg {...g}>
      <path d="M2.6 6a1.7 1.7 0 0 1 1.7-1.7h2.9L9 6.4h6.7A1.7 1.7 0 0 1 17.4 8v6.3a1.7 1.7 0 0 1-1.7 1.7H4.3a1.7 1.7 0 0 1-1.7-1.7z" />
    </svg>
  );
}

export function SettingsIcon() {
  return (
    <svg {...g}>
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2.4l.5 2.1a5.9 5.9 0 0 1 1.8.75l1.85-1.15 1.9 1.9-1.15 1.85c.33.55.59 1.16.75 1.8l2.1.5v2.7l-2.1.5a5.9 5.9 0 0 1-.75 1.8l1.15 1.85-1.9 1.9-1.85-1.15a5.9 5.9 0 0 1-1.8.75l-.5 2.1H8.65l-.5-2.1a5.9 5.9 0 0 1-1.8-.75L4.5 18.6l-1.9-1.9 1.15-1.85a5.9 5.9 0 0 1-.75-1.8l-2.1-.5V9.85l2.1-.5c.16-.64.42-1.25.75-1.8L2.6 5.7l1.9-1.9 1.85 1.15a5.9 5.9 0 0 1 1.8-.75l.5-2.1z" />
    </svg>
  );
}

export function SyncIcon() {
  return (
    <svg {...g}>
      <path d="M16.6 10a6.6 6.6 0 1 1-1.95-4.68" />
      <path d="M16.8 2.9v3.4h-3.4" />
    </svg>
  );
}

export function MenuIcon() {
  return (
    <svg {...g} strokeWidth={1.4}>
      <path d="M3.4 5.6h13.2M3.4 10h13.2M3.4 14.4h13.2" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg {...g}>
      <circle cx="8.8" cy="8.8" r="5.4" />
      <path d="M12.8 12.8 17 17" />
    </svg>
  );
}

export function SortIcon() {
  return (
    <svg {...g}>
      <path d="M3.6 5.4h12.8M3.6 10h8.4M3.6 14.6h4.8" />
    </svg>
  );
}

export function PrintIcon() {
  return (
    <svg {...g}>
      <path d="M5.6 7V2.9h8.8V7" />
      <path d="M5.6 14.4H4A1.4 1.4 0 0 1 2.6 13V9.2A1.4 1.4 0 0 1 4 7.8h12a1.4 1.4 0 0 1 1.4 1.4V13a1.4 1.4 0 0 1-1.4 1.4h-1.6" />
      <rect x="5.6" y="11.6" width="8.8" height="5.5" rx="1" />
    </svg>
  );
}

export function AddIcon() {
  return (
    <svg {...g} strokeWidth={1.5}>
      <path d="M10 4.2v11.6M4.2 10h11.6" />
    </svg>
  );
}

/** The command-bar overflow. Three dots, the way Windows draws "more". */
export function MoreIcon() {
  return (
    <svg {...g} strokeWidth={2.2}>
      <path d="M5 10h.01M10 10h.01M15 10h.01" />
    </svg>
  );
}

/** A tag in the navigation pane. */
export function TagIcon() {
  return (
    <svg {...g}>
      <path d="M3.2 8.4V4.6a1.4 1.4 0 0 1 1.4-1.4h3.8a1.4 1.4 0 0 1 1 .41l7 7a1.4 1.4 0 0 1 0 1.98l-3.8 3.8a1.4 1.4 0 0 1-1.98 0l-7-7a1.4 1.4 0 0 1-.42-1z" />
      <path d="M6.6 6.6h.01" strokeWidth="2" />
    </svg>
  );
}

export function BackIcon() {
  return (
    <svg {...g}>
      <path d="M12.4 4.4 6.8 10l5.6 5.6" />
    </svg>
  );
}

export function RestoreIcon() {
  return (
    <svg {...g}>
      <path d="M3.4 10a6.6 6.6 0 1 0 1.95-4.68" />
      <path d="M3.2 2.9v3.4h3.4" />
    </svg>
  );
}

export function DeleteIcon() {
  return DeletedIcon();
}

export const SMART_ICONS = {
  today: TodayIcon,
  upcoming: UpcomingIcon,
  all: AllIcon,
  completed: CompletedIcon,
  deleted: DeletedIcon,
};

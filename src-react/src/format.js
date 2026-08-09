/**
 * Display helpers. Everything on the wire is a tz-aware UTC instant; display is
 * local.
 *
 * An all-day reminder's instant is local midnight of its day, which is a
 * position on the timeline rather than a time anyone chose. So it is never
 * shown with a clock time, and it does not go late until its day is over --
 * midnight passing is not the same as being overdue.
 */

export function formatDue(iso, allDay = false) {
  if (!iso) return "";
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: sameYear ? undefined : "numeric",
    hour: allDay ? undefined : "2-digit",
    minute: allDay ? undefined : "2-digit",
  });
}

export function formatTime(iso, allDay = false) {
  if (!iso) return "";
  if (allDay) return "All Day";
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Midnight ending the local day that `d` falls in. */
function endOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  x.setDate(x.getDate() + 1);
  return x.getTime();
}

export function dueClass(iso, completed, allDay = false) {
  if (!iso || completed) return "";
  const d = new Date(iso);
  const due = allDay ? endOfDay(d) : d.getTime();
  const now = Date.now();
  if (due < now) return "overdue";
  if (due < now + 24 * 3600 * 1000) return "soon";
  return "";
}

/** Local wall-clock for <input type="datetime-local">, not UTC. */
export function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(
    d.getHours()
  )}:${p(d.getMinutes())}`;
}

/**
 * Reshape a due-input value when the all-day toggle moves.
 *
 * The two inputs speak different dialects -- `date` wants "2026-08-07",
 * `datetime-local` wants "2026-08-07T09:00" -- and handing either the other's
 * string silently blanks the field. Both editors share this so they cannot
 * disagree about which way round it goes.
 */
export function reshapeDue(value, allDay) {
  if (!value) return "";
  if (allDay) return value.slice(0, 10);
  // Coming back from all-day there is no time to restore, so pick a sensible
  // one rather than 00:00, which reads as "no time" all over again.
  return value.length > 10 ? value : `${value}T09:00`;
}

export const PRIORITIES = [
  { value: 0, label: "None" },
  { value: 1, label: "High" },
  { value: 5, label: "Medium" },
  { value: 9, label: "Low" },
];

/* ------------------------------------------------------- quick date picking */

/** "2026-08-09" for a Date, in the local zone rather than UTC. */
export function ymd(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The next `weekday` strictly after `from` -- never `from` itself. */
function nextWeekday(from, weekday) {
  const d = new Date(from);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + (((weekday - d.getDay() + 7) % 7) || 7));
  return d;
}

/**
 * The four presets Apple's own quick menu offers, with the day number each one
 * lands on -- which is the whole point of showing a calendar glyph rather than
 * a generic one. "Next weekend" is the coming Saturday and "next week" the
 * coming Monday, both strictly ahead, so neither can ever resolve to today.
 */
export function datePresets(now = new Date()) {
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);

  return [
    { key: "today", label: "Today", date: today },
    { key: "tomorrow", label: "Tomorrow", date: tomorrow },
    { key: "weekend", label: "Next Weekend", date: nextWeekday(today, 6) },
    { key: "week", label: "Next Week", date: nextWeekday(today, 1) },
  ].map((p) => ({ ...p, value: ymd(p.date), day: p.date.getDate() }));
}

/**
 * How a chosen date reads on the button once it is set: the preset's own word
 * where one matches, otherwise a real date. Apple shows "Tomorrow" rather than
 * "10 Aug" for as long as that stays true, and re-resolves it the next day.
 */
export function dueLabel(value, now = new Date()) {
  if (!value) return "";
  const hit = datePresets(now).find((p) => p.value === value);
  if (hit && hit.key !== "weekend" && hit.key !== "week") return hit.label;
  const [y, m, d] = value.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

/** Common times, offered before the picker -- most reminders want one of them. */
export const TIME_PRESETS = ["09:00", "12:00", "17:00", "20:00"];

/** "17:00" in whatever form the user's locale writes five in the afternoon. */
export function formatClock(hhmm) {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export const priorityLabel = (p) =>
  (PRIORITIES.find((x) => x.value === Number(p)) || PRIORITIES[0]).label;

/** Apple shows priority as exclamation marks: low !, medium !!, high !!!. */
export const priorityMarks = (p) => ({ 1: "!!!", 5: "!!", 9: "!" }[Number(p)] || "");

const startOfDay = (d) => {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
};

const weekday = (d) => d.toLocaleDateString(undefined, { weekday: "long" });

/**
 * A day heading in Apple's style: Overdue, Today, Tomorrow, then a real date.
 * Grouped by local calendar day, matching how the sidecar buckets Today.
 */
export function dayHeading(iso) {
  if (!iso) return { key: "none", label: "No Date", order: 9e15 };
  const d = startOfDay(new Date(iso));
  const today = startOfDay(new Date());
  const days = Math.round((d - today) / 86400000);
  const order = d.getTime();

  if (days < 0) return { key: "overdue", label: "Overdue", order: -1 };
  if (days === 0) return { key: "d0", label: "Today", sub: weekday(d), order };
  if (days === 1) return { key: "d1", label: "Tomorrow", sub: weekday(d), order };

  const sameYear = d.getFullYear() === today.getFullYear();
  return {
    key: `d${order}`,
    label: d.toLocaleDateString(undefined, {
      month: "long",
      day: "numeric",
      year: sameYear ? undefined : "numeric",
    }),
    sub: weekday(d),
    order,
  };
}

/**
 * "about 2 min left", from elapsed time and how far along we are.
 *
 * Two guards, both because a bad estimate is worse than none: nothing before
 * 8%, since extrapolating from the first list of fourteen swings by minutes
 * between ticks, and nothing in the first few seconds, when the elapsed time
 * itself is too small to divide by. A delta sync never gets one -- its size
 * isn't known in advance.
 */
export function etaText(sync) {
  if (!sync || !sync.determinate || !sync.startedAt) return "";
  const p = Number(sync.percent) || 0;
  const elapsed = (Date.now() - sync.startedAt) / 1000;
  if (p < 8 || p >= 100 || elapsed < 4) return "";
  const remaining = Math.round((elapsed * (100 - p)) / p);
  if (remaining < 5) return "almost done";
  if (remaining < 60) return `about ${Math.max(5, Math.round(remaining / 5) * 5)}s left`;
  return `about ${Math.round(remaining / 60)} min left`;
}

/**
 * The headline, kept short enough not to truncate. List names are long and the
 * sidebar is narrow, so the name goes on the second line where losing its tail
 * costs nothing.
 */
export function syncLabel(sync) {
  if (!sync) return "";
  if (sync.stage === "lists") return "Reading lists…";
  if (sync.stage === "changes") return "Applying changes…";
  if (sync.stage === "reminders" && sync.of) {
    return `${sync.done} of ${sync.of} lists`;
  }
  return sync.determinate ? "Syncing…" : "Checking for changes…";
}

/** Second line: what it's on right now, and how long that leaves. */
export function syncDetail(sync) {
  if (!sync) return "";
  const eta = etaText(sync);
  const count = sync.total ? `${sync.total.toLocaleString()} reminders` : "";
  return [sync.list || count, eta].filter(Boolean).join(" · ");
}

export const SORTS = [
  { value: "manual", label: "Default" },
  { value: "due", label: "Due Date" },
  { value: "priority", label: "Priority" },
  { value: "title", label: "Title" },
  { value: "created", label: "Recently Added" },
];

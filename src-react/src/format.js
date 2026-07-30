/** Display helpers. Everything on the wire is tz-aware UTC; display is local. */

export function formatDue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: sameYear ? undefined : "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function dueClass(iso, completed) {
  if (!iso || completed) return "";
  const due = new Date(iso).getTime();
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

export const PRIORITIES = [
  { value: 0, label: "None" },
  { value: 1, label: "High" },
  { value: 5, label: "Medium" },
  { value: 9, label: "Low" },
];

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

export const SORTS = [
  { value: "manual", label: "Default" },
  { value: "due", label: "Due Date" },
  { value: "priority", label: "Priority" },
  { value: "title", label: "Title" },
  { value: "created", label: "Recently Added" },
];

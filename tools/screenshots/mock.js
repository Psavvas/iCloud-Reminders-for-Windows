// Stubs the Tauri bridge so src/index.html renders in a plain browser for
// screenshots. Data mirrors the shape and scale of the real account.
(function () {
  const iso = (d) => d.toISOString();
  const now = new Date();
  const rel = (h) => iso(new Date(now.getTime() + h * 3600 * 1000));

  // A local wall-clock time, which is what the sidecar sends after resolving
  // Apple's floating due dates. `at(0, 20, 0)` is 8 PM tonight.
  const at = (dayOffset, hour, minute) => {
    const d = new Date();
    d.setDate(d.getDate() + dayOffset);
    d.setHours(hour, minute, 0, 0);
    return iso(d);
  };

  const LISTS = [
    { id: "List/G", title: "School", color_hex: null, is_group: 1, open_count: 0, count: 0 },
    { id: "List/CS", title: "AP Computer Science A", color_hex: "#5AC8FA", is_group: 0, open_count: 5 },
    { id: "List/GOV", title: "AP Government", color_hex: "#FFCC00", is_group: 0, open_count: 26 },
    { id: "List/PHY", title: "STEM AP Physics 1", color_hex: "#AC7F5E", is_group: 0, open_count: 135 },
    { id: "List/PP", title: "Personal Projects", color_hex: "#FF9500", is_group: 0, open_count: 293 },
    { id: "List/FUT", title: "Future", color_hex: "#5AC8FA", is_group: 0, open_count: 20 },
    { id: "List/BIO", title: "STEM Biology Honors", color_hex: "#83D754", is_group: 0, open_count: 26 },
    { id: "List/PRE", title: "STEM Precalculus Honors", color_hex: "#FF383C", is_group: 0, open_count: 326 },
    { id: "List/ENG", title: "STEM Pre-AP English 10", color_hex: "#CC73E1", is_group: 0, open_count: 9 },
    { id: "List/ENGR", title: "STEM Engineering", color_hex: "#5856D6", is_group: 0, open_count: 45 },
    { id: "List/PER", title: "Personal", color_hex: "#FF2D55", is_group: 0, open_count: 8 },
    { id: "List/GRO", title: "Family Groceries", color_hex: "#63DA38", is_group: 0, open_count: 12 },
    { id: "List/CHO", title: "Chores", color_hex: "#FFCC00", is_group: 0, open_count: 1219 },
    { id: "List/IN", title: "Inbox", color_hex: "#CC73E1", is_group: 0, open_count: 817 },
  ];

  const REMINDERS = [
    { id: "R/1", list_id: "List/IN", title: "Renew library books", description: "Three overdue — the art history one is on hold for someone else.", due_date: rel(-30), priority: 1, completed: 0, dirty: 0, tags: ["errands"], modified: rel(-40) },
    { id: "R/2", list_id: "List/IN", title: "Book dentist appointment", description: "", due_date: rel(-2), priority: 5, completed: 0, dirty: 0, tags: ["health"], modified: rel(-5) },
    { id: "R/3", list_id: "List/IN", title: "Submit KIPR STL registration", description: "Deadline is firm. Need the team roster finalised first.", due_date: rel(3), priority: 1, completed: 0, dirty: 1, tags: ["school", "urgent"], modified: rel(-1) },
    { id: "R/4", list_id: "List/IN", title: "Pick up dry cleaning", description: "", due_date: rel(6), priority: 0, completed: 0, dirty: 0, tags: ["errands"] },
    { id: "R/5", list_id: "List/IN", title: "Email Mr. Halvorsen about the lab writeup", description: "", due_date: rel(22), priority: 9, completed: 0, dirty: 0, tags: ["school"] },
    { id: "R/6", list_id: "List/IN", title: "Replace the bike tube", description: "", due_date: rel(30), priority: 0, completed: 0, dirty: 0, tags: [] },
    { id: "R/7", list_id: "List/IN", title: "Draft the physics lab conclusion", description: "", due_date: rel(52), priority: 5, completed: 0, dirty: 0, tags: ["school"] },
    { id: "R/8", list_id: "List/IN", title: "Order birthday present for Nina", description: "", due_date: null, priority: 0, completed: 0, dirty: 0, tags: ["personal"] },
    { id: "R/9", list_id: "List/IN", title: "Back up the Pi before reflashing", description: "", due_date: null, priority: 0, completed: 0, dirty: 0, tags: [] },
    { id: "R/10", list_id: "List/IN", title: "Cancel TAP ticket", description: "", due_date: rel(-160), priority: 0, completed: 1, dirty: 0, tags: [] },
    { id: "R/11", list_id: "List/IN", title: "Submit KIPR STL reg (old draft)", description: "", due_date: rel(-200), priority: 0, completed: 0, deleted: 1, dirty: 0, tags: [] },
    { id: "R/12", list_id: "List/CHO", title: "Take out recycling", description: "", due_date: null, priority: 0, completed: 0, deleted: 1, dirty: 0, tags: [] },
    // The two cases the due-date fix is about. Both used to read as overdue:
    // the 8 PM one because the stored instant was shifted by the UTC offset,
    // the all-day one because its midnight instant landed on the previous
    // evening entirely.
    { id: "R/13", list_id: "List/IN", title: "Reset CSM email", description: "", due_date: at(0, 20, 0), priority: 0, completed: 0, dirty: 0, tags: [] },
    { id: "R/14", list_id: "List/IN", title: "Pay the water bill", description: "", due_date: at(0, 0, 0), all_day: 1, priority: 5, completed: 0, dirty: 0, tags: [] },
    { id: "R/15", list_id: "List/IN", title: "Mum's birthday", description: "", due_date: at(2, 0, 0), all_day: 1, priority: 0, completed: 0, dirty: 0, tags: ["personal"] },
  ];

  // Chores is the long list on the real account. Filling it out here is what
  // makes the list actually scroll, which is the only way to see the scrollbar.
  const CHORES = [
    "Wipe down the kitchen counters", "Descale the kettle", "Sort the recycling",
    "Change the bed linen", "Hoover the stairs", "Water the plants",
    "Clean the bathroom mirror", "Take the bins out", "Refill the salt grinder",
    "Wash the car", "Sweep the porch", "Defrost the freezer",
    "Replace the smoke alarm battery", "Clear out the fridge",
    "Iron the school shirts", "Mop the hallway", "Dust the bookshelves",
  ];
  CHORES.forEach((title, i) => {
    REMINDERS.push({
      id: `R/C${i}`, list_id: "List/CHO", title, description: "",
      due_date: i % 3 === 0 ? at(1 + (i % 5), 9 + (i % 8), 0) : null,
      priority: [0, 0, 5, 0, 9][i % 5], completed: 0, dirty: 0, tags: [],
    });
  });

  const TAGS = [
    { name: "errands", n: 12 }, { name: "health", n: 3 }, { name: "personal", n: 9 },
    { name: "school", n: 41 }, { name: "urgent", n: 4 },
  ];

  const settings = {
    theme: "system", sync_minutes: 10, notifications_enabled: true,
    stale_after_minutes: 60, max_individual_toasts: 3,
    default_list_id: null, search_scope: "list",
    onboarded: true, print_group_by: "due", sort_by: {},
    print_include_notes: true, print_include_completed: false,
  };

  const startOfTomorrow = () => {
    const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + 1); return d;
  };

  const handlers = {
    auth_status: () => ({
      authenticated: !window.__MOCK_SIGNED_OUT && !window.__MOCK_RESTORING,
      apple_id: "you@icloud.com",
      has_cache: !window.__MOCK_SIGNED_OUT && !window.__MOCK_RESTORING,
      restoring: !!window.__MOCK_RESTORING,
      can_restore: !!window.__MOCK_RESTORING,
    }),
    settings: () => ({ ...settings, onboarded: !window.__MOCK_ONBOARD }),
    set_settings: (p) => Object.assign(settings, p),
    smart_counts: () => {
      const t = startOfTomorrow();
      const live = REMINDERS.filter((r) => !r.deleted);
      return {
        today: live.filter((r) => !r.completed && r.due_date && new Date(r.due_date) < t).length,
        upcoming: live.filter((r) => !r.completed && r.due_date && new Date(r.due_date) >= t).length,
        completed: live.filter((r) => r.completed).length,
        deleted: REMINDERS.filter((r) => r.deleted).length,
        all: live.filter((r) => !r.completed).length,
      };
    },
    restore_reminder: (p) => {
      const r = REMINDERS.find((x) => x.id === p.id);
      if (r) r.deleted = 0;
      return r;
    },
    sign_out: () => ({ signed_out: true }),
    lists: () => LISTS,
    tags: () => TAGS,
    reminders: (p) => {
      const t = startOfTomorrow();
      let rows = REMINDERS.slice();
      rows = rows.filter((r) => (p.scope === "deleted" ? r.deleted : !r.deleted));
      if (p.scope === "today") {
        rows = rows.filter((r) => !r.completed && r.due_date && new Date(r.due_date) < t);
      } else if (p.scope === "upcoming") {
        rows = rows.filter((r) => !r.completed && r.due_date && new Date(r.due_date) >= t);
      } else if (p.scope === "completed") {
        rows = rows.filter((r) => r.completed);
      } else if (p.scope !== "deleted" && !p.include_completed) {
        rows = rows.filter((r) => !r.completed);
      }
      if (p.list_id) rows = rows.filter((r) => r.list_id === p.list_id);
      if (p.tag) rows = rows.filter((r) => (r.tags || []).includes(p.tag));
      if (p.search) {
        const q = p.search.toLowerCase();
        rows = rows.filter((r) => r.title.toLowerCase().includes(q));
      }
      const byDue = (a, b) => {
        if (!a.due_date && !b.due_date) return 0;
        if (!a.due_date) return 1;
        if (!b.due_date) return -1;
        return new Date(a.due_date) - new Date(b.due_date);
      };
      const rank = (p) => ({ 1: 0, 5: 1, 9: 2 }[Number(p)] ?? 3);
      const cmp = {
        title: (a, b) => a.title.localeCompare(b.title),
        priority: (a, b) => rank(a.priority) - rank(b.priority) || byDue(a, b),
        created: (a, b) => String(b.id).localeCompare(String(a.id)),
        due: byDue,
      }[p.sort] || byDue;
      rows = rows.sort(cmp);
      // The Completed view is capped server-side.
      if (p.scope === "completed") rows = rows.slice(0, 50);
      return rows;
    },
    reminder: (p) => REMINDERS.find((r) => r.id === p.id) || null,
    sync_status: () => ({
      running: false, last_sync: rel(-0.2), has_cursor: true,
      pending_pushes: 1, conflicts: window.__MOCK_CONFLICT ? 1 : 0,
      sync_minutes: settings.sync_minutes,
    }),
    conflicts: () => (window.__MOCK_CONFLICT ? [{
      id: 1,
      local: { id: "R/3", title: "Submit KIPR STL registration" },
      remote: { id: "R/3", title: "Submit KIPR STL reg (moved to Friday)" },
    }] : []),
    sync: () => ({ queued: true }),
    login: () => ({ authenticated: true }),
    update_reminder: (p) => {
      const r = REMINDERS.find((x) => x.id === p.id);
      if (r) Object.assign(r, p);
      return r;
    },
    delete_reminder: (p) => {
      const r = REMINDERS.find((x) => x.id === p.id);
      if (r) r.deleted = 1;
      return { deleted: p.id };
    },
    create_reminder: (p) => {
      const r = { id: `R/new${REMINDERS.length}`, tags: [], dirty: 1, ...p };
      REMINDERS.unshift(r);
      return r;
    },
  };

  // A real event bus, so a capture can drive sidecar events -- the sync bar
  // only exists while one is in flight and cannot be screenshotted otherwise.
  const subscribers = new Map();
  window.__emit = (event, payload) => {
    for (const fn of subscribers.get(event) || []) fn({ payload });
  };

  window.__TAURI__ = {
    core: {
      invoke: async (cmd, args) => {
        if (cmd === "sidecar_status") return { running: true, error: null, tried_paths: [] };
        if (cmd === "get_autostart") return false;
        if (cmd === "set_autostart") return !!(args && args.enabled);
        const { method, params } = args || {};
        const fn = handlers[method];
        if (!fn) throw JSON.stringify({ code: "NO_METHOD", message: method });
        return fn(params || {});
      },
    },
    event: {
      listen: async (event, fn) => {
        const list = subscribers.get(event) || [];
        list.push(fn);
        subscribers.set(event, list);
        return () => subscribers.set(event, (subscribers.get(event) || []).filter((x) => x !== fn));
      },
    },
  };
})();

// Stubs the Tauri bridge so src/index.html renders in a plain browser for
// screenshots. Data mirrors the shape and scale of the real account.
(function () {
  const iso = (d) => d.toISOString();
  const now = new Date();
  const rel = (h) => iso(new Date(now.getTime() + h * 3600 * 1000));

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
  ];

  const TAGS = [
    { name: "errands", n: 12 }, { name: "health", n: 3 }, { name: "personal", n: 9 },
    { name: "school", n: 41 }, { name: "urgent", n: 4 },
  ];

  const handlers = {
    auth_status: () => ({ authenticated: true, apple_id: "you@icloud.com", has_cache: true }),
    lists: () => LISTS,
    tags: () => TAGS,
    reminders: (p) => {
      let rows = REMINDERS.slice();
      if (p.list_id) rows = rows.filter((r) => r.list_id === p.list_id);
      if (p.tag) rows = rows.filter((r) => (r.tags || []).includes(p.tag));
      if (!p.include_completed) rows = rows.filter((r) => !r.completed);
      if (p.search) {
        const q = p.search.toLowerCase();
        rows = rows.filter((r) => r.title.toLowerCase().includes(q));
      }
      return rows.sort((a, b) => {
        if (!a.due_date && !b.due_date) return 0;
        if (!a.due_date) return 1;
        if (!b.due_date) return -1;
        return new Date(a.due_date) - new Date(b.due_date);
      });
    },
    reminder: (p) => REMINDERS.find((r) => r.id === p.id) || null,
    sync_status: () => ({
      running: false, last_sync: rel(-0.2), has_cursor: true,
      pending_pushes: 1, conflicts: window.__MOCK_CONFLICT ? 1 : 0,
    }),
    conflicts: () => (window.__MOCK_CONFLICT ? [{
      id: 1,
      local: { id: "R/3", title: "Submit KIPR STL registration" },
      remote: { id: "R/3", title: "Submit KIPR STL reg (moved to Friday)" },
    }] : []),
    sync: () => ({ queued: true }),
    login: () => ({ authenticated: true }),
  };

  window.__TAURI__ = {
    core: {
      invoke: async (cmd, args) => {
        if (cmd === "sidecar_status") return { running: true, error: null, tried_paths: [] };
        const { method, params } = args || {};
        const fn = handlers[method];
        if (!fn) throw JSON.stringify({ code: "NO_METHOD", message: method });
        return fn(params || {});
      },
    },
    event: { listen: async () => () => {} },
  };
})();

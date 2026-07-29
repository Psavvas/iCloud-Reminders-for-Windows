/**
 * UI logic.
 *
 * Every read is a sidecar call served from SQLite, so nothing here waits on
 * iCloud. Writes update the cache immediately and drain to iCloud in the
 * background; the row shows a pending dot until it lands.
 */

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const call = (method, params = {}) => invoke("call", { method, params });

const state = {
  lists: [],
  tags: [],
  counts: {},
  settings: {},
  // Exactly one of these three is active at a time.
  scope: "today",
  selectedList: null,
  selectedTag: null,
  selectedReminder: null,
  search: "",
  searchScope: "list",
  showDone: false,
};

const $ = (id) => document.getElementById(id);

const SMART = [
  { key: "today", label: "Today", glyph: "◉", color: "#007aff" },
  { key: "upcoming", label: "Upcoming", glyph: "▤", color: "#ff3b30" },
  { key: "all", label: "All", glyph: "≡", color: "#8e8e93" },
  { key: "completed", label: "Completed", glyph: "✓", color: "#34c759" },
  { key: "deleted", label: "Deleted", glyph: "✕", color: "#8e8e93" },
];

// ------------------------------------------------------------------ helpers

/** Store UTC, display local. Dates are always tz-aware on the wire. */
function formatDue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: sameYear ? undefined : "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function dueClass(iso, completed) {
  if (!iso || completed) return "";
  const due = new Date(iso).getTime();
  const now = Date.now();
  if (due < now) return "overdue";
  if (due < now + 24 * 3600 * 1000) return "soon";
  return "";
}

/** Local wall-clock for <input type="datetime-local">, not UTC. */
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

const PRIORITIES = [
  { value: 0, label: "None" },
  { value: 1, label: "High" },
  { value: 5, label: "Medium" },
  { value: 9, label: "Low" },
];

function priorityLabel(p) {
  return (PRIORITIES.find((x) => x.value === Number(p)) || PRIORITIES[0]).label;
}

/** Apple shows priority as exclamation marks: low !, medium !!, high !!!. */
function priorityMarks(p) {
  return { 1: "!!!", 5: "!!", 9: "!" }[Number(p)] || "";
}

function banner(message, kind = "info", timeout = 6000) {
  const el = $("banner");
  el.textContent = message;
  el.className = `banner ${kind}`;
  if (timeout) setTimeout(() => el.classList.add("hidden"), timeout);
}

/** Errors arrive as a JSON string from the sidecar; pull out the useful bits. */
function parseError(e) {
  try {
    const o = typeof e === "string" ? JSON.parse(e) : e;
    return {
      code: o.code || "ERROR",
      message: o.message || String(e),
      detail: o.detail || "",
    };
  } catch {
    return { code: "ERROR", message: String(e), detail: "" };
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
}

// --------------------------------------------------------------------- auth

function showStep(id) {
  for (const s of document.querySelectorAll(".gate-step")) s.classList.add("hidden");
  if (id) $(id).classList.remove("hidden");
}

function gateError(msg) {
  const el = $("gate-error");
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
}

/** Render the "sidecar isn't running" gate, including where we looked. */
async function showSidecarDown(detail) {
  showStep("gate-sidecar");
  let text = detail || "";
  try {
    const st = await invoke("sidecar_status");
    if (st.error) text = st.error;
    if (st.tried_paths && st.tried_paths.length) {
      text += "\n\nLooked in:\n" + st.tried_paths.join("\n");
    }
  } catch {
    /* status is best-effort; the detail we already have still helps */
  }
  $("sidecar-detail").textContent = text.trim();
}

$("retry-sidecar").addEventListener("click", async () => {
  showStep("gate-loading");
  try {
    await invoke("restart_sidecar");
    await boot();
  } catch (e) {
    await showSidecarDown(String(e));
  }
});

async function boot() {
  try {
    const st = await call("auth_status");
    try {
      state.settings = await call("settings");
      applyTheme(state.settings.theme);
      state.searchScope = state.settings.search_scope || "list";
    } catch { /* defaults are fine */ }

    if (st.authenticated) return enterApp();
    if (st.has_cache) {
      // Cache is usable even without a session, so show the data and let the
      // user re-auth when they choose to.
      enterApp();
      banner("Your iCloud session expired — sign in again to sync.", "warn", 0);
      return;
    }
    showStep("gate-login");
    if (st.apple_id) $("apple-id").value = st.apple_id;
  } catch (e) {
    const err = parseError(e);
    if (err.code === "SIDECAR_DOWN") return showSidecarDown(err.detail || err.message);
    showStep("gate-login");
    gateError(err.message);
  }
}

$("gate-login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  gateError("");
  $("gate-loading-text").textContent = "Signing in…";
  showStep("gate-loading");
  try {
    await call("login", {
      apple_id: $("apple-id").value.trim(),
      password: $("password").value,
    });
    enterApp();
  } catch (e) {
    const { code, message, detail } = parseError(e);
    if (code === "2FA_REQUIRED") {
      try { await call("request_2fa"); } catch { /* may already be sent */ }
      showStep("gate-2fa");
      $("code").focus();
    } else if (code === "TERMS_REQUIRED") {
      showStep("gate-terms");
    } else if (code === "SIDECAR_DOWN") {
      await showSidecarDown(detail || message);
    } else {
      showStep("gate-login");
      gateError(message);
    }
  }
});

$("gate-2fa").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  gateError("");
  try {
    await call("submit_2fa", { code: $("code").value.trim() });
    enterApp();
  } catch (e) {
    gateError(parseError(e).message);
  }
});

$("resend-2fa").addEventListener("click", async () => {
  try {
    await call("request_2fa");
    banner("New code sent.", "ok", 3000);
  } catch (e) {
    gateError(parseError(e).message);
  }
});

$("accept-terms").addEventListener("click", async () => {
  showStep("gate-loading");
  try {
    await call("login", {
      apple_id: $("apple-id").value.trim(),
      password: $("password").value,
      accept_terms: true,
    });
    enterApp();
  } catch (e) {
    showStep("gate-login");
    gateError(parseError(e).message);
  }
});

async function enterApp() {
  $("gate").classList.add("hidden");
  $("app").classList.remove("hidden");
  await refreshAll();
}

// ------------------------------------------------------------------ render

async function refreshAll() {
  await Promise.all([loadLists(), loadTags(), loadCounts()]);
  await loadReminders();
  await refreshSyncStatus();
}

async function loadCounts() {
  try {
    state.counts = await call("smart_counts");
  } catch {
    state.counts = {};
  }
  renderSmart();
}

function renderSmart() {
  const nav = $("smart");
  nav.innerHTML = "";
  for (const s of SMART) {
    const row = document.createElement("button");
    const active = state.scope === s.key && !state.selectedList && !state.selectedTag;
    row.className = "smart-row" + (active ? " active" : "");
    row.innerHTML = `
      <span class="glyph" style="background:${s.color}">${s.glyph}</span>
      <span class="list-name"></span>
      <span class="count">${state.counts[s.key] ?? ""}</span>`;
    row.querySelector(".list-name").textContent = s.label;
    row.onclick = () => selectScope(s.key);
    nav.appendChild(row);
  }
}

async function loadLists() {
  state.lists = await call("lists");
  const nav = $("lists");
  nav.innerHTML = "";

  for (const l of state.lists) {
    const row = document.createElement("button");
    const active = state.selectedList === l.id && !state.selectedTag;
    row.className = "list-row" + (active ? " active" : "") + (l.is_group ? " group" : "");
    // Group rows have no colour of their own; fall back to neutral grey.
    const colour = l.color_hex || "#8E8E93";
    row.innerHTML = `<span class="dot" style="background:${colour}"></span>
      <span class="list-name"></span>
      <span class="count">${l.open_count ?? 0}</span>`;
    row.querySelector(".list-name").textContent = l.title;
    row.onclick = () => selectList(l.id);
    nav.appendChild(row);
    // With 14 lists the sidebar scrolls; keep the selected one on screen.
    if (active) requestAnimationFrame(() => row.scrollIntoView({ block: "nearest" }));
  }
}

async function loadTags() {
  state.tags = await call("tags");
  const box = $("tags");
  box.innerHTML = "";
  if (!state.tags.length) {
    box.innerHTML = `<span class="hint tiny">No tags yet.</span>`;
    return;
  }
  for (const t of state.tags) {
    const chip = document.createElement("button");
    chip.className = "chip" + (state.selectedTag === t.name ? " active" : "");
    chip.textContent = `#${t.name}`;
    chip.onclick = () => selectTag(state.selectedTag === t.name ? null : t.name);
    box.appendChild(chip);
  }
}

function selectScope(key) {
  state.scope = key;
  state.selectedList = null;
  state.selectedTag = null;
  redraw();
}

function selectList(id) {
  state.selectedList = id;
  state.scope = null;
  state.selectedTag = null;
  redraw();
}

function selectTag(name) {
  state.selectedTag = name;
  state.selectedList = null;
  state.scope = name ? null : "today";
  redraw();
}

function redraw() {
  renderSmart();
  loadLists();
  loadTags();
  loadReminders();
}

/** What the header should say, given the current selection. */
function currentTitle() {
  if (state.selectedTag) return { text: `#${state.selectedTag}`, color: "" };
  if (state.selectedList) {
    const l = state.lists.find((x) => x.id === state.selectedList);
    return { text: l ? l.title : "Reminders", color: (l && l.color_hex) || "" };
  }
  const s = SMART.find((x) => x.key === state.scope);
  return { text: s ? s.label : "Reminders", color: s ? s.color : "" };
}

async function loadReminders() {
  // Global search ignores the current selection; that's the point of it.
  const globalSearch = state.search && state.searchScope === "global";
  const params = {
    include_completed: state.showDone,
    search: state.search || null,
  };
  if (!globalSearch) {
    params.list_id = state.selectedList;
    params.tag = state.selectedTag;
    params.scope = state.scope;
  }

  const rows = await call("reminders", params);

  const t = globalSearch ? { text: "All Reminders", color: "" } : currentTitle();
  $("list-title").textContent = t.text;
  $("list-title").style.color = t.color || "";

  const note = $("result-note");
  if (state.search) {
    note.textContent = `${rows.length} result${rows.length === 1 ? "" : "s"} ${
      globalSearch ? "everywhere" : "in this list"
    }`;
  } else {
    note.textContent = "";
  }

  const ul = $("reminders");
  ul.innerHTML = "";
  $("empty").classList.toggle("hidden", rows.length > 0);
  $("empty").textContent = state.search
    ? "No matches."
    : state.scope === "deleted"
    ? "Nothing deleted."
    : state.scope === "completed"
    ? "Nothing completed yet."
    : "Nothing here.";

  const inTrash = state.scope === "deleted";

  for (const r of rows) {
    const li = document.createElement("li");
    li.className = "reminder" + (r.completed ? " done" : "");
    if (state.selectedReminder === r.id) li.classList.add("selected");

    if (inTrash) {
      // A checkbox makes no sense on a deleted row; offer the undo instead.
      const restore = document.createElement("button");
      restore.className = "restore-btn";
      restore.textContent = "↺";
      restore.title = "Put back";
      restore.onclick = async (ev) => {
        ev.stopPropagation();
        await call("restore_reminder", { id: r.id });
        await loadReminders();
        await loadCounts();
        await loadLists();
        banner("Restored.", "ok", 2500);
      };
      li.appendChild(restore);
    } else {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !!r.completed;
      box.onclick = async (ev) => {
        ev.stopPropagation();
        await call("update_reminder", { id: r.id, completed: box.checked });
        await loadReminders();
        await loadCounts();
        await loadLists();
      };
      li.appendChild(box);
    }

    const main = document.createElement("div");
    main.className = "reminder-main";
    const title = document.createElement("div");
    title.className = "reminder-title";
    title.textContent = r.title || "(untitled)";
    main.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "reminder-meta";
    if (priorityMarks(r.priority)) {
      const p = document.createElement("span");
      p.className = "prio p" + r.priority;
      p.title = priorityLabel(r.priority);
      p.textContent = priorityMarks(r.priority);
      meta.appendChild(p);
    }
    if (r.due_date) {
      const due = document.createElement("span");
      due.className = "due " + dueClass(r.due_date, r.completed);
      due.textContent = formatDue(r.due_date);
      meta.appendChild(due);
    }
    // Which list a reminder is in only matters when the view spans lists --
    // which a global search does even while a list is selected.
    if (!state.selectedList || globalSearch) {
      const l = state.lists.find((x) => x.id === r.list_id);
      if (l) {
        const chip = document.createElement("span");
        chip.className = "list-chip";
        chip.style.color = l.color_hex || "";
        chip.textContent = l.title;
        meta.appendChild(chip);
      }
    }
    for (const t of r.tags || []) {
      const tag = document.createElement("span");
      tag.className = "pill tag";
      tag.textContent = `#${t}`;
      meta.appendChild(tag);
    }
    if (r.dirty) {
      const d = document.createElement("span");
      d.className = "pending";
      d.title = "Not yet synced to iCloud";
      meta.appendChild(d);
    }
    if (meta.childNodes.length) main.appendChild(meta);

    li.appendChild(main);
    li.onclick = () => showDetail(r.id);
    ul.appendChild(li);
  }
}

// ------------------------------------------------------------------ detail

async function showDetail(id) {
  state.selectedReminder = id;
  const r = await call("reminder", { id });
  const pane = $("detail");
  if (!r) {
    pane.innerHTML = `<p class="empty">Select a reminder.</p>`;
    return;
  }

  pane.innerHTML = `
    <div class="detail-body">
      <label>Title</label>
      <input id="d-title" type="text" />

      <label>Notes</label>
      <textarea id="d-desc" rows="6"></textarea>

      <label>Due</label>
      <div class="row">
        <input id="d-due" type="datetime-local" />
        <button id="d-due-clear" class="ghost" type="button">Clear</button>
      </div>

      <label>Priority</label>
      <select id="d-priority">
        ${PRIORITIES.map(
          (p) => `<option value="${p.value}">${p.label}</option>`
        ).join("")}
      </select>

      <label>List</label>
      <div class="static" id="d-list"></div>

      <label>Tags</label>
      <div class="static" id="d-tags"></div>

      <div class="detail-actions">
        <button id="d-save" class="primary" type="button">Save</button>
        <button id="d-delete" class="danger" type="button">Delete</button>
      </div>
      <p class="hint tiny" id="d-state"></p>
    </div>`;

  $("d-title").value = r.title || "";
  $("d-desc").value = r.description || "";
  $("d-due").value = toLocalInput(r.due_date);
  $("d-priority").value = String(Number(r.priority) || 0);
  $("d-list").textContent =
    (state.lists.find((l) => l.id === r.list_id) || {}).title || r.list_id;
  $("d-tags").textContent = (r.tags || []).map((t) => `#${t}`).join("  ") || "—";
  $("d-state").textContent = r.dirty
    ? "Pending sync to iCloud."
    : r.modified
    ? `Last changed ${formatDue(r.modified)}`
    : "";

  $("d-due-clear").onclick = () => ($("d-due").value = "");

  $("d-save").onclick = async () => {
    // datetime-local has no zone. Send it naked and let the sidecar resolve it
    // against the local zone -- Apple would otherwise read it as UTC.
    const dueRaw = $("d-due").value;
    await call("update_reminder", {
      id: r.id,
      title: $("d-title").value,
      description: $("d-desc").value,
      due_date: dueRaw ? dueRaw + ":00" : null,
      priority: Number($("d-priority").value),
    });
    await loadReminders();
    await loadCounts();
    await showDetail(r.id);
    banner("Saved.", "ok", 2500);
  };

  $("d-delete").onclick = async () => {
    await call("delete_reminder", { id: r.id });
    state.selectedReminder = null;
    pane.innerHTML = `<p class="empty">Select a reminder.</p>`;
    await loadReminders();
    await loadCounts();
    await loadLists();
  };
}

// -------------------------------------------------------------- new reminder

function defaultListId() {
  return (
    state.settings.default_list_id ||
    state.selectedList ||
    (state.lists.find((l) => (l.title || "").toLowerCase() === "inbox") ||
      state.lists.find((l) => !l.is_group) ||
      {}).id
  );
}

function fillListSelect(sel, includeNone = false) {
  sel.innerHTML = includeNone ? `<option value="">Inbox (default)</option>` : "";
  for (const l of state.lists.filter((x) => !x.is_group)) {
    const opt = document.createElement("option");
    opt.value = l.id;
    opt.textContent = l.title;
    sel.appendChild(opt);
  }
}

function openNewDialog() {
  const dlg = $("new-dialog");
  if (!state.lists.length) return banner("No lists loaded yet.", "warn");

  fillListSelect($("new-list"));
  $("new-list").value = defaultListId() || $("new-list").options[0]?.value;

  $("new-priority").innerHTML = PRIORITIES.map(
    (p) => `<option value="${p.value}">${p.label}</option>`
  ).join("");
  $("new-priority").value = "0";

  $("new-title").value = "";
  $("new-notes").value = "";
  $("new-due").value = "";

  dlg.showModal();
  $("new-title").focus();
}

$("new-btn").addEventListener("click", openNewDialog);
$("new-cancel").addEventListener("click", () => $("new-dialog").close());
$("new-due-clear").addEventListener("click", () => ($("new-due").value = ""));

// Clicking the backdrop dismisses, matching how Apple's sheets behave.
for (const id of ["new-dialog", "settings-dialog"]) {
  $(id).addEventListener("click", (ev) => {
    if (ev.target === $(id)) $(id).close();
  });
}

$("new-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const title = $("new-title").value.trim();
  if (!title) return;
  const listId = $("new-list").value;
  if (!listId) return banner("No list to add to.", "warn");

  const due = $("new-due").value;
  $("new-dialog").close();

  await call("create_reminder", {
    list_id: listId,
    title,
    description: $("new-notes").value,
    due_date: due ? due + ":00" : null,
    priority: Number($("new-priority").value),
  });
  await loadReminders();
  await loadCounts();
  await loadLists();
});

// ----------------------------------------------------------------- settings

async function openSettings() {
  const dlg = $("settings-dialog");
  const s = (state.settings = await call("settings"));

  $("set-theme").value = s.theme || "system";
  $("set-sync").value = String(s.sync_minutes || 10);
  $("set-search-scope").value = s.search_scope || "list";
  $("set-notify").checked = s.notifications_enabled !== false;
  $("set-stale").value = String(s.stale_after_minutes || 60);

  fillListSelect($("set-default-list"), true);
  $("set-default-list").value = s.default_list_id || "";

  try {
    $("set-autostart").checked = await invoke("get_autostart");
    $("autostart-note").textContent = "";
  } catch (e) {
    $("set-autostart").disabled = true;
    $("autostart-note").textContent = "Couldn't read the startup setting: " + e;
  }

  $("set-account").textContent = state.appleId || "Signed in";
  dlg.showModal();
}

async function saveSettings(patch) {
  state.settings = await call("set_settings", patch);
  if ("theme" in patch) applyTheme(state.settings.theme);
  if ("search_scope" in patch) {
    state.searchScope = state.settings.search_scope;
    updateScopeButton();
  }
}

$("settings-btn").addEventListener("click", openSettings);
$("settings-close").addEventListener("click", () => $("settings-dialog").close());

$("set-theme").addEventListener("change", (e) => saveSettings({ theme: e.target.value }));
$("set-sync").addEventListener("change", (e) =>
  saveSettings({ sync_minutes: Number(e.target.value) })
);
$("set-search-scope").addEventListener("change", (e) =>
  saveSettings({ search_scope: e.target.value })
);
$("set-notify").addEventListener("change", (e) =>
  saveSettings({ notifications_enabled: e.target.checked })
);
$("set-stale").addEventListener("change", (e) =>
  saveSettings({ stale_after_minutes: Number(e.target.value) })
);
$("set-default-list").addEventListener("change", (e) =>
  saveSettings({ default_list_id: e.target.value || null })
);

$("set-autostart").addEventListener("change", async (e) => {
  try {
    const on = await invoke("set_autostart", { enabled: e.target.checked });
    e.target.checked = on;
  } catch (err) {
    e.target.checked = !e.target.checked;
    $("autostart-note").textContent = "Couldn't change it: " + err;
  }
});

$("set-full-sync").addEventListener("click", async () => {
  await call("sync", { full: true });
  $("settings-dialog").close();
  banner("Re-downloading everything…", "info", 4000);
});

$("set-signout").addEventListener("click", async () => {
  await call("sign_out", {});
  $("settings-dialog").close();
  $("app").classList.add("hidden");
  $("gate").classList.remove("hidden");
  showStep("gate-login");
});

// ------------------------------------------------------------------- search

function updateScopeButton() {
  $("search-scope").textContent =
    state.searchScope === "global" ? "Everywhere" : "In List";
  $("search-scope").classList.toggle("global", state.searchScope === "global");
}

function openSearch() {
  $("search-wrap").classList.add("open");
  $("search").focus();
}

function closeSearch() {
  $("search-wrap").classList.remove("open");
  if (state.search) {
    state.search = "";
    $("search").value = "";
    loadReminders();
  }
}

$("search-btn").addEventListener("click", openSearch);
$("search-close").addEventListener("click", closeSearch);

$("search-scope").addEventListener("click", () => {
  state.searchScope = state.searchScope === "global" ? "list" : "global";
  updateScopeButton();
  saveSettings({ search_scope: state.searchScope });
  if (state.search) loadReminders();
});

let searchTimer = null;
$("search").addEventListener("input", (e) => {
  state.search = e.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadReminders, 140);
});

$("search").addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    e.stopPropagation();
    closeSearch();
  }
});

$("show-done").addEventListener("change", (e) => {
  state.showDone = e.target.checked;
  loadReminders();
});

$("sync-btn").addEventListener("click", async () => {
  await call("sync", {});
  banner("Syncing…", "info", 2000);
});

// ---------------------------------------------------------------- shortcuts

document.addEventListener("keydown", (ev) => {
  const dlg = $("new-dialog");
  const settings = $("settings-dialog");
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  const inApp = !$("app").classList.contains("hidden");
  const mod = ev.ctrlKey || ev.metaKey;

  if (mod && ev.key.toLowerCase() === "n") {
    ev.preventDefault();
    if (!dlg.open) openNewDialog();
  } else if (mod && ev.key.toLowerCase() === "f") {
    ev.preventDefault();
    if (inApp) openSearch();
  } else if (mod && ev.key === ",") {
    ev.preventDefault();
    if (inApp && !settings.open) openSettings();
  } else if (ev.key === "n" && !typing && !dlg.open && inApp) {
    ev.preventDefault();
    openNewDialog();
  }
});

// ------------------------------------------------------------------- status

async function refreshSyncStatus() {
  try {
    const st = await call("sync_status");
    const bits = [];
    if (st.running) bits.push("Syncing…");
    else if (st.last_sync) bits.push(`Synced ${formatDue(st.last_sync)}`);
    else bits.push("Not synced yet");
    if (st.pending_pushes) bits.push(`${st.pending_pushes} pending`);
    if (st.conflicts) bits.push(`${st.conflicts} conflict(s)`);
    $("sync-status").textContent = bits.join(" · ");
    if (st.conflicts) showConflicts();
  } catch {
    $("sync-status").textContent = "";
  }
}

async function showConflicts() {
  const rows = await call("conflicts");
  if (!rows.length) return;
  const c = rows[0];
  banner(
    `"${c.local.title || "A reminder"}" changed on another device. ` +
      `Keeping the iCloud version; your edit is saved.`,
    "warn",
    0
  );
  const el = $("banner");
  const keep = document.createElement("button");
  keep.className = "ghost";
  keep.textContent = "Use my version";
  keep.onclick = async () => {
    await call("resolve_conflict", { id: c.id, keep: "local" });
    el.classList.add("hidden");
    await loadReminders();
    await refreshSyncStatus();
  };
  const drop = document.createElement("button");
  drop.className = "ghost";
  drop.textContent = "Keep iCloud's";
  drop.onclick = async () => {
    await call("resolve_conflict", { id: c.id, keep: "remote" });
    el.classList.add("hidden");
    await refreshSyncStatus();
  };
  el.appendChild(keep);
  el.appendChild(drop);
}

// ------------------------------------------------------------------- events

listen("sidecar://sync_finished", async () => {
  await refreshAll();
});
listen("sidecar://sync_progress", (e) => {
  const d = e.payload || {};
  if (d.stage === "reminders") {
    $("sync-status").textContent = `Syncing ${d.index}/${d.of} — ${d.total} reminders`;
  }
});
listen("sidecar://sync_error", (e) => {
  const err = e.payload || {};
  if (err.code === "AUTH_REQUIRED") {
    banner("Your iCloud session expired — sign in again to sync.", "warn", 0);
  } else if (err.code === "TERMS_REQUIRED") {
    banner("Apple needs you to accept updated iCloud terms.", "warn", 0);
  } else {
    banner(err.message || "Sync failed.", "warn");
  }
});
listen("sidecar://conflict", () => refreshSyncStatus());
listen("sidecar://died", (e) => {
  const err = (e.payload || {}).error || "";
  if (!$("app").classList.contains("hidden")) {
    banner("The sync service stopped. Reconnecting…", "warn", 0);
  } else {
    showSidecarDown(err);
  }
});
listen("sidecar://ready", () => boot());
listen("sidecar://restarted", () => {
  banner("Sync service reconnected.", "ok", 3000);
  boot();
});
listen("app://notified", () => {
  loadReminders();
  loadCounts();
});

setInterval(refreshSyncStatus, 15000);

updateScopeButton();
boot();

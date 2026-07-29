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
  selectedList: null,
  selectedTag: null,
  selectedReminder: null,
  search: "",
  showDone: false,
};

const $ = (id) => document.getElementById(id);

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
      try { await call("request_2fa"); } catch { /* code may already be sent */ }
      showStep("gate-2fa");
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
  await Promise.all([loadLists(), loadTags()]);
  await loadReminders();
  await refreshSyncStatus();
}

async function loadLists() {
  state.lists = await call("lists");
  const nav = $("lists");
  nav.innerHTML = "";

  const all = document.createElement("button");
  all.className = "list-row" + (state.selectedList === null && !state.selectedTag ? " active" : "");
  all.innerHTML = `<span class="dot" style="background:#8E8E93"></span>
    <span class="list-name">All</span>`;
  all.onclick = () => selectList(null);
  nav.appendChild(all);

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

function selectList(id) {
  state.selectedList = id;
  state.selectedTag = null;
  loadLists();
  loadTags();
  loadReminders();
}

function selectTag(name) {
  state.selectedTag = name;
  state.selectedList = null;
  loadLists();
  loadTags();
  loadReminders();
}

async function loadReminders() {
  const rows = await call("reminders", {
    list_id: state.selectedList,
    tag: state.selectedTag,
    include_completed: state.showDone,
    search: state.search || null,
  });

  // Apple tints the list heading with the list's own colour.
  const current = state.lists.find((l) => l.id === state.selectedList);
  $("list-title").textContent = state.selectedTag
    ? `#${state.selectedTag}`
    : current
    ? current.title
    : "All";
  $("list-title").style.color =
    current && current.color_hex ? current.color_hex : "";

  const ul = $("reminders");
  ul.innerHTML = "";
  $("empty").classList.toggle("hidden", rows.length > 0);

  for (const r of rows) {
    const li = document.createElement("li");
    li.className = "reminder" + (r.completed ? " done" : "");
    if (state.selectedReminder === r.id) li.classList.add("selected");

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!r.completed;
    box.onclick = async (ev) => {
      ev.stopPropagation();
      await call("update_reminder", { id: r.id, completed: box.checked });
      loadReminders();
      loadLists();
    };

    const main = document.createElement("div");
    main.className = "reminder-main";
    const title = document.createElement("div");
    title.className = "reminder-title";
    title.textContent = r.title || "(untitled)";
    main.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "reminder-meta";
    if (r.due_date) {
      const due = document.createElement("span");
      due.className = "due " + dueClass(r.due_date, r.completed);
      due.textContent = formatDue(r.due_date);
      meta.appendChild(due);
    }
    if (priorityMarks(r.priority)) {
      const p = document.createElement("span");
      p.className = "prio p" + r.priority;
      p.title = priorityLabel(r.priority);
      p.textContent = priorityMarks(r.priority);
      // Priority reads first in Apple's layout, before the date.
      meta.insertBefore(p, meta.firstChild);
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
      d.textContent = "•";
      meta.appendChild(d);
    }
    if (meta.childNodes.length) main.appendChild(meta);

    li.appendChild(box);
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
    await showDetail(r.id);
    banner("Saved.", "ok", 2500);
  };

  $("d-delete").onclick = async () => {
    await call("delete_reminder", { id: r.id });
    state.selectedReminder = null;
    pane.innerHTML = `<p class="empty">Select a reminder.</p>`;
    await loadReminders();
    await loadLists();
  };
}

// ------------------------------------------------------------------ actions

$("quick-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const title = $("quick-title").value.trim();
  if (!title) return;
  const listId =
    state.selectedList ||
    (state.lists.find((l) => l.title.toLowerCase() === "inbox") ||
      state.lists.find((l) => !l.is_group) ||
      {}).id;
  if (!listId) return banner("No list to add to.", "warn");
  await call("create_reminder", { list_id: listId, title });
  $("quick-title").value = "";
  await loadReminders();
  await loadLists();
});

$("search").addEventListener("input", (e) => {
  state.search = e.target.value.trim();
  loadReminders();
});

$("show-done").addEventListener("change", (e) => {
  state.showDone = e.target.checked;
  loadReminders();
});

$("sync-btn").addEventListener("click", async () => {
  await call("sync", {});
  banner("Syncing…", "info", 2000);
});

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
  // If we never got past the gate, show the actionable panel rather than a
  // banner the user cannot do anything about.
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
listen("app://notified", () => loadReminders());

setInterval(refreshSyncStatus, 15000);

boot();

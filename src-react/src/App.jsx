import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call, invoke, listen, parseError } from "./bridge.js";
import Gate from "./components/Gate.jsx";
import Sidebar from "./components/Sidebar.jsx";
import ListPane from "./components/ListPane.jsx";
import Detail from "./components/Detail.jsx";
import NewReminderSheet from "./components/NewReminderSheet.jsx";
import SettingsSheet from "./components/SettingsSheet.jsx";
import PrintSheet from "./components/PrintSheet.jsx";
import Onboarding from "./components/Onboarding.jsx";
import Banner from "./components/Banner.jsx";
import AuthNotice from "./components/AuthNotice.jsx";
import UpdateNotice from "./components/UpdateNotice.jsx";

const SMART = [
  { key: "today", label: "Today", glyph: "◉", color: "#007aff" },
  { key: "upcoming", label: "Upcoming", glyph: "▤", color: "#ff3b30" },
  { key: "all", label: "All", glyph: "≡", color: "#8e8e93" },
  { key: "completed", label: "Completed", glyph: "✓", color: "#34c759" },
  { key: "deleted", label: "Deleted", glyph: "✕", color: "#8e8e93" },
];

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
  // Mirrored into localStorage so the inline script in index.html can apply it
  // on the next launch before the first paint. Settings remains the source of
  // truth; this is only ever a head start.
  try {
    if (theme === "light" || theme === "dark") localStorage.setItem("theme", theme);
    else localStorage.removeItem("theme");
  } catch {
    /* storage unavailable; the round trip still sets it, just later */
  }
}

/** How long a row spends fading out before the list reloads without it. */
const ROW_LEAVE_MS = 220;

const reducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export default function App() {
  const [phase, setPhase] = useState("loading"); // loading | gate | app
  const [gateStep, setGateStep] = useState("loading");
  const [gateError, setGateError] = useState("");
  const [sidecarDetail, setSidecarDetail] = useState("");

  const [settings, setSettings] = useState({});
  const [lists, setLists] = useState([]);
  const [tags, setTags] = useState([]);
  const [counts, setCounts] = useState({});
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState({});
  const [conflicts, setConflicts] = useState([]);

  // Exactly one of scope / listId / tag is active.
  const [scope, setScope] = useState("today");
  const [listId, setListId] = useState(null);
  const [tag, setTag] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState("");
  const [searchScope, setSearchScope] = useState("list");
  const [showDone, setShowDone] = useState(false);
  const [sheet, setSheet] = useState(null); // new | settings | print
  // Carried from the inline composer into the full sheet, so pressing Details
  // does not throw away what has already been typed.
  const [newTitle, setNewTitle] = useState("");
  const [onboarding, setOnboarding] = useState(false);
  const [banner, setBanner] = useState(null);
  // Sticky, unlike `banner`. Set when the session dies while the app is open,
  // cleared only by signing back in -- see AuthNotice for why it is not a toast.
  const [authExpired, setAuthExpired] = useState(false);
  // Version string once the shell has found a newer release, else null.
  const [updateVersion, setUpdateVersion] = useState(null);
  // Null when idle; otherwise { determinate, percent, stage, ... } for the bar.
  const [sync, setSync] = useState(null);

  const toast = useCallback((message, kind = "info", timeout = 5000) => {
    setBanner({ message, kind, timeout, at: Date.now() });
  }, []);

  // ------------------------------------------------------------- sort state
  // Per-list, so "Chores by due date" doesn't reorder every other list too.
  const sortKey = tag ? `tag:${tag}` : listId ? `list:${listId}` : `smart:${scope}`;
  const sortBy = (settings.sort_by || {})[sortKey] || "manual";

  const setSortBy = useCallback(
    async (value) => {
      const next = { ...(settings.sort_by || {}), [sortKey]: value };
      const saved = await call("set_settings", { sort_by: next });
      setSettings(saved);
    },
    [settings.sort_by, sortKey]
  );

  // ----------------------------------------------------------------- loading
  const globalSearch = Boolean(search) && searchScope === "global";

  const loadRows = useCallback(async () => {
    const params = {
      include_completed: showDone,
      search: search || null,
      sort: sortBy,
    };
    if (!globalSearch) {
      params.list_id = listId;
      params.tag = tag;
      params.scope = scope;
    }
    try {
      setRows(await call("reminders", params));
    } catch (e) {
      const err = parseError(e);
      if (err.code === "SIDECAR_DOWN") {
        setSidecarDetail(err.detail || err.message);
        setPhase("gate");
        setGateStep("sidecar");
      }
    }
  }, [showDone, search, sortBy, globalSearch, listId, tag, scope]);

  const loadShell = useCallback(async () => {
    const [l, t, c] = await Promise.all([
      call("lists"),
      call("tags"),
      call("smart_counts"),
    ]);
    setLists(l);
    setTags(t);
    setCounts(c);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const st = await call("sync_status");
      setStatus(st);
      setConflicts(st.conflicts ? await call("conflicts") : []);
    } catch {
      /* status is cosmetic */
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await loadShell();
    await loadRows();
    await refreshStatus();
  }, [loadShell, loadRows, refreshStatus]);

  // ------------------------------------------------------------------- boot
  const boot = useCallback(async () => {
    try {
      const st = await call("auth_status");
      let cfg = {};
      try {
        cfg = await call("settings");
        setSettings(cfg);
        applyTheme(cfg.theme);
        setSearchScope(cfg.search_scope || "list");
      } catch {
        /* defaults are fine */
      }

      if (st.authenticated || st.has_cache) {
        setPhase("app");
        setAuthExpired(!st.authenticated && !st.restoring);
        await refreshAll();
        if (st.authenticated && !cfg.onboarded) {
          setOnboarding(true);
        }
        return;
      }
      if (st.restoring) {
        // Signed in on a previous run and the sidecar is rebuilding the session
        // right now. Showing the password form here is what made the app feel
        // like it signed you out constantly. `restoring` and not `can_restore`:
        // a saved password outlives a restore that failed, and waiting on an
        // auth_changed that already fired would hang here forever.
        setPhase("gate");
        setGateStep("restoring");
        return;
      }
      setPhase("gate");
      setGateStep("login");
    } catch (e) {
      const err = parseError(e);
      setPhase("gate");
      if (err.code === "SIDECAR_DOWN") {
        setSidecarDetail(err.detail || err.message);
        setGateStep("sidecar");
      } else {
        setGateStep("login");
        setGateError(err.message);
      }
    }
  }, [refreshAll, toast]);

  useEffect(() => {
    boot();
    // Intentionally once: boot re-runs via sidecar events, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (phase === "app") loadRows();
  }, [phase, loadRows]);

  // ----------------------------------------------------------------- events
  useEffect(() => {
    const offs = [
      listen("sidecar://sync_started", (e) => {
        const d = e.payload || {};
        setSync({
          determinate: d.determinate !== false,
          percent: 0,
          startedAt: Date.now(),
          stage: null,
        });
      }),
      listen("sidecar://sync_finished", async () => {
        setSync(null);
        await refreshAll();
      }),
      listen("sidecar://sync_progress", (e) => {
        const d = e.payload || {};
        setSync((s) =>
          s
            ? { ...s, ...d, percent: d.percent ?? s.percent }
            : // A progress event with no start (the sidecar restarted mid-sync)
              // still deserves a bar.
              { determinate: d.percent != null, startedAt: Date.now(), ...d }
        );
      }),
      listen("sidecar://sync_error", (e) => {
        const err = e.payload || {};
        setSync(null);
        if (err.code === "AUTH_REQUIRED") {
          // Deliberately not a toast: this one has to outlive the next message.
          setAuthExpired(true);
        } else if (err.code === "TERMS_REQUIRED") {
          toast("Apple needs you to accept updated iCloud terms.", "warn", 0);
        } else {
          toast(err.message || "Sync failed.", "warn");
        }
      }),
      listen("sidecar://auth_changed", (e) => {
        const st = e.payload || {};
        if (st.authenticated) {
          setAuthExpired(false);
          boot();
          return;
        }
        // The restore finished and failed. Only now is the password form the
        // right thing to show — not on the way there. Someone already looking
        // at cached data gets told rather than silently left with stale rows.
        setGateStep((s) => (s === "restoring" ? "login" : s));
        if (phase === "app") setAuthExpired(true);
      }),
      listen("sidecar://conflict", refreshStatus),
      listen("sidecar://ready", boot),
      listen("sidecar://restarted", () => {
        toast("Sync service reconnected.", "ok", 3000);
        boot();
      }),
      listen("sidecar://died", (e) => {
        const err = (e.payload || {}).error || "";
        if (phase === "app") toast("The sync service stopped. Reconnecting…", "warn", 0);
        else {
          setSidecarDetail(err);
          setPhase("gate");
          setGateStep("sidecar");
        }
      }),
      listen("app://notified", () => {
        loadRows();
        loadShell();
      }),
      listen("app://update_available", (e) => {
        setUpdateVersion((e.payload || {}).version || null);
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [boot, loadRows, loadShell, refreshAll, refreshStatus, toast, phase]);

  useEffect(() => {
    const id = setInterval(refreshStatus, 15000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  // -------------------------------------------------------------- selection
  const select = useCallback((next) => {
    setScope(next.scope ?? null);
    setListId(next.listId ?? null);
    setTag(next.tag ?? null);
    setSelectedId(null);
  }, []);

  const current = useMemo(() => {
    if (globalSearch) return { text: "All Reminders", color: "" };
    if (tag) return { text: `#${tag}`, color: "" };
    if (listId) {
      const l = lists.find((x) => x.id === listId);
      return { text: l ? l.title : "Reminders", color: (l && l.color_hex) || "" };
    }
    const s = SMART.find((x) => x.key === scope);
    return { text: s ? s.label : "Reminders", color: s ? s.color : "" };
  }, [globalSearch, tag, listId, lists, scope]);

  // --------------------------------------------------------------- mutations
  const mutate = useCallback(
    async (fn) => {
      await fn();
      await loadRows();
      await loadShell();
    },
    [loadRows, loadShell]
  );

  // Typed straight into the list, so only a title exists. Everything else
  // follows the view: the default list (or the one being looked at), and in
  // Today a due date of today -- without which the reminder is created and
  // immediately invisible, which reads as the app having lost it.
  const quickCreate = useCallback(
    (title) => {
      const payload = {
        list_id: settings.default_list_id || listId || lists.find((l) => !l.is_group)?.id,
        title,
      };
      if (!payload.list_id) {
        toast("No list to add to yet.", "warn");
        return;
      }
      if (scope === "today" && !listId && !tag) {
        const d = new Date();
        const p = (n) => String(n).padStart(2, "0");
        payload.due_date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
        payload.all_day = true;
      }
      mutate(() => call("create_reminder", payload));
    },
    [settings.default_list_id, listId, lists, scope, tag, mutate, toast]
  );

  const selected = useMemo(
    () => rows.find((r) => r.id === selectedId) || null,
    [rows, selectedId]
  );

  // Rows on their way out. Ticking something off in a view that hides completed
  // items removes it, and doing that in the same frame as the click reads as a
  // glitch -- so the row fades first and the reload waits for it.
  const [leaving, setLeaving] = useState(() => new Set());

  const toggleComplete = useCallback(
    async (r, done) => {
      const willLeave =
        done && !showDone && scope !== "completed" && !reducedMotion();
      if (willLeave) {
        setLeaving((s) => new Set(s).add(r.id));
        await new Promise((res) => setTimeout(res, ROW_LEAVE_MS));
      }
      try {
        await mutate(() => call("update_reminder", { id: r.id, completed: done }));
      } finally {
        // Always clear it: a failed write leaves the row in place, and a row
        // stuck at opacity 0 would look like the app had lost it.
        setLeaving((s) => {
          if (!s.has(r.id)) return s;
          const next = new Set(s);
          next.delete(r.id);
          return next;
        });
      }
    },
    [mutate, showDone, scope]
  );

  /** Identity of the current view, for replaying the enter animation. */
  const viewKey = tag ? `t:${tag}` : listId ? `l:${listId}` : `s:${scope}`;

  // -------------------------------------------------------------- shortcuts
  const searchRef = useRef(null);
  useEffect(() => {
    const onKey = (ev) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(
        document.activeElement?.tagName || ""
      );
      const mod = ev.ctrlKey || ev.metaKey;
      if (phase !== "app") return;
      if (mod && ev.key.toLowerCase() === "n") {
        ev.preventDefault();
        searchRef.current?.compose();
      } else if (mod && ev.key.toLowerCase() === "f") {
        ev.preventDefault();
        searchRef.current?.open();
      } else if (mod && ev.key.toLowerCase() === "p") {
        ev.preventDefault();
        setSheet("print");
      } else if (mod && ev.key === ",") {
        ev.preventDefault();
        setSheet("settings");
      } else if (ev.key === "n" && !typing && !sheet) {
        ev.preventDefault();
        searchRef.current?.compose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, sheet]);

  // ------------------------------------------------------------------ render
  if (phase !== "app") {
    return (
      <Gate
        step={gateStep}
        setStep={setGateStep}
        error={gateError}
        setError={setGateError}
        sidecarDetail={sidecarDetail}
        setSidecarDetail={setSidecarDetail}
        onSignedIn={boot}
      />
    );
  }

  return (
    <>
      <div className="app">
        <Sidebar
          smart={SMART}
          counts={counts}
          lists={lists}
          tags={tags}
          scope={scope}
          listId={listId}
          tag={tag}
          onSelect={select}
          status={status}
          sync={sync}
          onSync={async () => {
            await call("sync", {});
            toast("Syncing…", "info", 2000);
          }}
          onSettings={() => setSheet("settings")}
        />

        <ListPane
          ref={searchRef}
          title={current}
          rows={rows}
          lists={lists}
          scope={scope}
          listId={listId}
          globalSearch={globalSearch}
          search={search}
          setSearch={setSearch}
          searchScope={searchScope}
          onToggleSearchScope={async () => {
            const next = searchScope === "global" ? "list" : "global";
            setSearchScope(next);
            setSettings(await call("set_settings", { search_scope: next }));
          }}
          showDone={showDone}
          setShowDone={setShowDone}
          sortBy={sortBy}
          setSortBy={setSortBy}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onNew={(title) => {
            setNewTitle(title || "");
            setSheet("new");
          }}
          onQuickCreate={quickCreate}
          onPrint={() => setSheet("print")}
          viewKey={viewKey}
          leaving={leaving}
          onToggleComplete={toggleComplete}
          onRestore={(r) =>
            mutate(async () => {
              await call("restore_reminder", { id: r.id });
              toast("Restored.", "ok", 2500);
            })
          }
        />

        <Detail
          reminder={selected}
          lists={lists}
          onSave={(patch) =>
            mutate(async () => {
              await call("update_reminder", patch);
              toast("Saved.", "ok", 2000);
            })
          }
          onDelete={(r) =>
            mutate(async () => {
              await call("delete_reminder", { id: r.id });
              setSelectedId(null);
            })
          }
        />
      </div>

      {sheet === "new" && (
        <NewReminderSheet
          lists={lists}
          defaultListId={settings.default_list_id || listId}
          initialTitle={newTitle}
          onClose={() => {
            setNewTitle("");
            setSheet(null);
          }}
          onCreate={(payload) =>
            mutate(() => call("create_reminder", payload)).then(() => {
              setNewTitle("");
              setSheet(null);
            })
          }
        />
      )}

      {sheet === "settings" && (
        <SettingsSheet
          settings={settings}
          setSettings={setSettings}
          lists={lists}
          appleId={status.apple_id}
          authExpired={authExpired}
          onReauth={() => {
            setSheet(null);
            setPhase("gate");
            setGateStep("login");
          }}
          onClose={() => setSheet(null)}
          onFullSync={async () => {
            await call("sync", { full: true });
            setSheet(null);
            toast("Re-downloading everything…", "info", 4000);
          }}
          onSignOut={async () => {
            await call("sign_out", {});
            setSheet(null);
            setPhase("gate");
            setGateStep("login");
          }}
          onThemeChange={applyTheme}
        />
      )}

      {sheet === "print" && (
        <PrintSheet
          settings={settings}
          setSettings={setSettings}
          title={current.text}
          lists={lists}
          query={{
            include_completed: showDone,
            search: search || null,
            ...(globalSearch ? {} : { list_id: listId, tag, scope }),
          }}
          onClose={() => setSheet(null)}
        />
      )}

      {onboarding && (
        <Onboarding
          settings={settings}
          setSettings={setSettings}
          sync={sync}
          counts={counts}
          lists={lists}
          onDone={() => setOnboarding(false)}
        />
      )}

      {updateVersion && !authExpired && (
        <UpdateNotice
          version={updateVersion}
          onError={(msg) => toast("Couldn't install the update: " + msg, "warn")}
        />
      )}

      {authExpired && (
        <AuthNotice
          appleId={status.apple_id}
          onSignIn={() => {
            setSheet(null);
            setPhase("gate");
            setGateStep("login");
          }}
        />
      )}

      <Banner
        banner={banner}
        conflicts={conflicts}
        onResolve={async (id, keep) => {
          await call("resolve_conflict", { id, keep });
          await refreshAll();
        }}
        onDismiss={() => setBanner(null)}
      />
    </>
  );
}

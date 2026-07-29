// Windows: no console window behind the GUI in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Tauri shell.
//!
//! Owns the window, the tray, the two timers, and the sidecar process. It holds
//! no reminder state of its own -- every read is a sidecar call served from
//! SQLite, so the UI never waits on iCloud.

mod sidecar;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri_plugin_notification::NotificationExt;
use tokio::time::{interval, sleep, Duration};

use crate::sidecar::Sidecar;

/// Scheduler cadence. 30s is frequent enough that a due time never feels late
/// and cheap enough to be invisible: it's one indexed SQLite query.
const TICK_SECONDS: u64 = 30;

/// Fallback sync cadence when settings can't be read. Inside the 5-15 minute
/// band from the brief; Settings can move it within that range.
const SYNC_SECONDS_DEFAULT: u64 = 10 * 60;

/// The sidecar handle is optional and replaceable. If it fails to start we
/// still manage state, so commands return a diagnosable error rather than
/// Tauri's "state not managed", and the user can retry without reinstalling.
struct AppState {
    sidecar: Mutex<Option<Arc<Sidecar>>>,
    last_error: Mutex<Option<String>>,
    tried_paths: Mutex<Vec<String>>,
}

impl AppState {
    fn get(&self) -> Option<Arc<Sidecar>> {
        self.sidecar.lock().ok().and_then(|g| g.clone())
    }

    fn set(&self, sc: Option<Arc<Sidecar>>) {
        if let Ok(mut g) = self.sidecar.lock() {
            *g = sc;
        }
    }

    fn set_error(&self, err: Option<String>) {
        if let Ok(mut g) = self.last_error.lock() {
            *g = err;
        }
    }
}

/// Everywhere the sidecar could reasonably live, most specific first.
///
/// Installed builds get it from the resource dir; `tauri dev` may not stage
/// resources, so the repo's dist-sidecar is checked too. The env var overrides
/// everything, which is what the dev instructions in the README use.
fn candidate_paths(app: &AppHandle) -> Vec<PathBuf> {
    let exe_name = if cfg!(windows) {
        "reminders-sidecar.exe"
    } else {
        "reminders-sidecar"
    };
    let mut out = Vec::new();

    if let Ok(p) = std::env::var("REMINDERS_SIDECAR") {
        if !p.trim().is_empty() {
            out.push(PathBuf::from(p));
        }
    }
    if let Ok(dir) = app.path().resource_dir() {
        out.push(dir.join(exe_name));
        // `resources` globs can land in a subdirectory named after the source.
        out.push(dir.join("dist-sidecar").join(exe_name));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            out.push(dir.join(exe_name));
            // Dev: target\debug\ -> repo root\dist-sidecar\
            for up in [2usize, 3] {
                let mut d = dir.to_path_buf();
                for _ in 0..up {
                    d.pop();
                }
                out.push(d.join("dist-sidecar").join(exe_name));
            }
        }
    }
    out.push(PathBuf::from("dist-sidecar").join(exe_name));
    out
}

async fn start_sidecar(app: AppHandle) -> Result<Arc<Sidecar>, String> {
    let state = app.state::<AppState>();
    let candidates = candidate_paths(&app);

    let tried: Vec<String> = candidates.iter().map(|p| p.display().to_string()).collect();
    if let Ok(mut g) = state.tried_paths.lock() {
        *g = tried.clone();
    }

    let found = candidates.iter().find(|p| p.exists());
    let Some(path) = found else {
        return Err(format!(
            "Could not find the sync service. Run scripts\\build-sidecar.ps1 to build it.\n\nLooked in:\n{}",
            tried.join("\n")
        ));
    };

    let data_dir = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let args = vec!["--data-dir".to_string(), data_dir.display().to_string()];

    match Sidecar::spawn(app.clone(), path.clone(), args).await {
        Ok(sc) => {
            // Prove it actually answers before declaring success: a frozen exe
            // that starts and immediately dies would otherwise look healthy.
            match sc.call("ping", json!({})).await {
                Ok(_) => Ok(sc),
                Err(e) => Err(format!(
                    "The sync service started but did not respond ({e}).\nPath: {}",
                    path.display()
                )),
            }
        }
        Err(e) => Err(format!(
            "Could not start the sync service: {e:#}\nPath: {}",
            path.display()
        )),
    }
}

// ---------------------------------------------------------------- commands --

#[tauri::command]
async fn call(
    state: State<'_, AppState>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    const ALLOWED: &[&str] = &[
        "ping",
        "auth_status",
        "login",
        "submit_2fa",
        "request_2fa",
        "lists",
        "reminders",
        "reminder",
        "tags",
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "sync",
        "sync_status",
        "conflicts",
        "resolve_conflict",
        "smart_counts",
        "settings",
        "set_settings",
        "sign_out",
        "restore_reminder",
    ];
    if !ALLOWED.contains(&method.as_str()) {
        return Err(format!("method {method} is not exposed to the UI"));
    }

    let Some(sc) = state.get() else {
        let detail = state
            .last_error
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| "The sync service is not running.".into());
        // Shaped like the sidecar's own errors so the UI has one code path.
        return Err(json!({
            "code": "SIDECAR_DOWN",
            "message": "The sync service is not running.",
            "detail": detail,
        })
        .to_string());
    };

    sc.call(&method, params.unwrap_or(json!({}))).await
}

#[tauri::command]
fn get_autostart(app: AppHandle) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    let mgr = app.autolaunch();
    if enabled {
        mgr.enable().map_err(|e| e.to_string())?;
    } else {
        mgr.disable().map_err(|e| e.to_string())?;
    }
    mgr.is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
fn sidecar_status(state: State<'_, AppState>) -> Value {
    json!({
        "running": state.get().is_some(),
        "error": state.last_error.lock().ok().and_then(|g| g.clone()),
        "tried_paths": state.tried_paths.lock().ok().map(|g| g.clone()).unwrap_or_default(),
    })
}

#[tauri::command]
async fn restart_sidecar(app: AppHandle) -> Result<Value, String> {
    let state = app.state::<AppState>();
    state.set(None);
    match start_sidecar(app.clone()).await {
        Ok(sc) => {
            state.set(Some(sc));
            state.set_error(None);
            Ok(json!({ "running": true }))
        }
        Err(e) => {
            state.set_error(Some(e.clone()));
            Err(e)
        }
    }
}

// ----------------------------------------------------------------- timers --

/// 30s tick: ask the sidecar what to toast, then toast it.
///
/// The sidecar decides *what* fires (including collapsing a backlog from a
/// sleeping machine into one summary) and marks those reminders notified in the
/// same call, so a crash between deciding and showing costs one toast rather
/// than looping.
async fn notification_loop(app: AppHandle) {
    let mut ticker = interval(Duration::from_secs(TICK_SECONDS));
    loop {
        ticker.tick().await;
        let Some(sc) = app.state::<AppState>().get() else {
            continue; // sidecar down; the UI is already telling the user
        };
        let plan = match sc.call("due_notifications", json!({})).await {
            Ok(p) => p,
            Err(e) => {
                log::warn!("due_notifications failed: {e}");
                continue;
            }
        };
        let Some(toasts) = plan.get("toasts").and_then(|t| t.as_array()) else {
            continue;
        };
        for t in toasts {
            let title = t.get("title").and_then(|v| v.as_str()).unwrap_or("Reminder");
            let body = t.get("body").and_then(|v| v.as_str()).unwrap_or("");
            if let Err(e) = app.notification().builder().title(title).body(body).show() {
                log::error!("toast failed: {e}");
            }
        }
        if !toasts.is_empty() {
            let _ = app.emit("app://notified", plan.clone());
        }
    }
}

/// Background delta sync. Failures are logged and retried next tick; the UI
/// keeps serving the cache regardless.
async fn sync_loop(app: AppHandle) {
    // Ticks once a minute and syncs when enough of them have passed, so a
    // change to the interval in Settings takes effect without a restart.
    let mut ticker = interval(Duration::from_secs(60));
    let mut elapsed: u64 = 0;
    loop {
        ticker.tick().await;
        let Some(sc) = app.state::<AppState>().get() else {
            continue;
        };
        elapsed += 60;

        let every = sc
            .call("sync_status", json!({}))
            .await
            .ok()
            .and_then(|v| v.get("sync_minutes").and_then(|m| m.as_u64()))
            .map(|m| m.clamp(1, 60) * 60)
            .unwrap_or(SYNC_SECONDS_DEFAULT);

        if elapsed < every {
            continue;
        }
        elapsed = 0;
        if let Err(e) = sc.call("sync", json!({})).await {
            log::warn!("background sync failed: {e}");
            let _ = app.emit("app://sync_failed", json!({ "error": e }));
        }
    }
}

/// If the sidecar dies mid-session, bring it back rather than stranding the UI.
async fn supervise(app: AppHandle) {
    loop {
        sleep(Duration::from_secs(15)).await;
        let state = app.state::<AppState>();
        if state.get().is_some() {
            continue;
        }
        if let Ok(sc) = start_sidecar(app.clone()).await {
            log::info!("sidecar restarted");
            state.set(Some(sc));
            state.set_error(None);
            let _ = app.emit("sidecar://restarted", json!({}));
        }
    }
}

// -------------------------------------------------------------------- tray --

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Open Reminders", true, None::<&str>)?;
    let sync = MenuItem::with_id(app, "sync", "Sync now", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &sync, &quit])?;

    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("iCloud Reminders")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "sync" => {
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    if let Some(sc) = app.state::<AppState>().get() {
                        let _ = sc.call("sync", json!({})).await;
                    }
                });
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            // Left click restores, matching what Windows users expect.
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                if let Some(w) = tray.app_handle().get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        })
        .build(app)?;
    Ok(())
}

// -------------------------------------------------------------------- main --

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(|app| {
            let handle = app.handle().clone();

            // Managed before anything can fail, so a bad sidecar surfaces as a
            // real message instead of "state not managed".
            handle.manage(AppState {
                sidecar: Mutex::new(None),
                last_error: Mutex::new(None),
                tried_paths: Mutex::new(Vec::new()),
            });

            build_tray(&handle)?;

            // Start the sidecar off the setup path: a slow or failing spawn
            // must not stop the window from appearing.
            let h = handle.clone();
            tauri::async_runtime::spawn(async move {
                let state = h.state::<AppState>();
                match start_sidecar(h.clone()).await {
                    Ok(sc) => {
                        state.set(Some(sc));
                        state.set_error(None);
                        let _ = h.emit("sidecar://ready", json!({}));
                    }
                    Err(e) => {
                        log::error!("{e}");
                        state.set_error(Some(e.clone()));
                        let _ = h.emit("sidecar://died", json!({ "error": e }));
                    }
                }
            });

            tauri::async_runtime::spawn(notification_loop(handle.clone()));
            tauri::async_runtime::spawn(sync_loop(handle.clone()));
            tauri::async_runtime::spawn(supervise(handle.clone()));

            Ok(())
        })
        .on_window_event(|window, event| {
            // Close means "get out of the way", not "quit". The scheduler has to
            // keep running for due-date toasts to be worth anything.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            call,
            sidecar_status,
            restart_sidecar,
            get_autostart,
            set_autostart
        ])
        .run(tauri::generate_context!())
        .expect("error while running application");
}

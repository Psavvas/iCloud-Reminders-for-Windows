// Windows: no console window behind the GUI in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Tauri shell.
//!
//! Owns the window, the tray, the two timers, and the sidecar process. It holds
//! no reminder state of its own -- every read is a sidecar call served from
//! SQLite, so the UI never waits on iCloud.

mod sidecar;

use std::path::PathBuf;
use std::sync::Arc;

use serde_json::{json, Value};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri_plugin_notification::NotificationExt;
use tokio::time::{interval, Duration};

use crate::sidecar::Sidecar;

/// Scheduler cadence. 30s is frequent enough that a due time never feels late
/// and cheap enough to be invisible: it's one indexed SQLite query.
const TICK_SECONDS: u64 = 30;

/// Background sync cadence, inside the 5-15 minute band from the brief.
const SYNC_SECONDS: u64 = 10 * 60;

struct AppState {
    sidecar: Arc<Sidecar>,
}

// ---------------------------------------------------------------- commands --
// Thin pass-throughs. Keeping the sidecar's method names lets the frontend and
// the protocol stay in step without a translation layer in between.

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
    ];
    if !ALLOWED.contains(&method.as_str()) {
        return Err(format!("method {method} is not exposed to the UI"));
    }
    state
        .sidecar
        .call(&method, params.unwrap_or(json!({})))
        .await
}

// ----------------------------------------------------------------- timers --

/// 30s tick: ask the sidecar what to toast, then toast it.
///
/// The sidecar decides *what* fires (including collapsing a backlog from a
/// sleeping machine into one summary) and marks those reminders notified in the
/// same call, so a crash between deciding and showing costs one toast rather
/// than looping.
async fn notification_loop(app: AppHandle, sidecar: Arc<Sidecar>) {
    let mut ticker = interval(Duration::from_secs(TICK_SECONDS));
    loop {
        ticker.tick().await;
        let plan = match sidecar.call("due_notifications", json!({})).await {
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
async fn sync_loop(app: AppHandle, sidecar: Arc<Sidecar>) {
    let mut ticker = interval(Duration::from_secs(SYNC_SECONDS));
    loop {
        ticker.tick().await;
        match sidecar.call("sync", json!({})).await {
            Ok(_) => {}
            Err(e) => {
                log::warn!("background sync failed: {e}");
                let _ = app.emit("app://sync_failed", json!({ "error": e }));
            }
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
                    let state = app.state::<AppState>();
                    let _ = state.sidecar.call("sync", json!({})).await;
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

/// Where the bundled sidecar lives next to the installed exe.
fn sidecar_path(app: &AppHandle) -> PathBuf {
    let name = if cfg!(windows) {
        "reminders-sidecar.exe"
    } else {
        "reminders-sidecar"
    };
    app.path()
        .resource_dir()
        .map(|d| d.join(name))
        .unwrap_or_else(|_| PathBuf::from(name))
}

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

            let program = std::env::var("REMINDERS_SIDECAR")
                .map(PathBuf::from)
                .unwrap_or_else(|_| sidecar_path(&handle));

            let data_dir = handle
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let args = vec!["--data-dir".to_string(), data_dir.display().to_string()];

            tauri::async_runtime::block_on(async {
                match Sidecar::spawn(handle.clone(), program, args).await {
                    Ok(sc) => {
                        handle.manage(AppState { sidecar: sc.clone() });
                        tauri::async_runtime::spawn(notification_loop(handle.clone(), sc.clone()));
                        tauri::async_runtime::spawn(sync_loop(handle.clone(), sc));
                    }
                    Err(e) => {
                        log::error!("could not start sidecar: {e:#}");
                        let _ = handle.emit(
                            "sidecar://died",
                            json!({ "error": format!("{e:#}") }),
                        );
                    }
                }
            });

            build_tray(&handle)?;
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
        .invoke_handler(tauri::generate_handler![call])
        .run(tauri::generate_context!())
        .expect("error while running application");
}

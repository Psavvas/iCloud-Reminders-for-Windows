//! Talks to the Python sidecar over stdio.
//!
//! One JSON object per line in each direction. Requests carry an `id` and are
//! matched to responses through a pending map; lines that arrive without an
//! `id` are unsolicited events (sync progress, conflicts, auth trouble) and get
//! forwarded straight to the webview.
//!
//! stdout is protocol traffic only. The sidecar logs to stderr, which we drain
//! into the Rust log so a wedged child is diagnosable instead of silent.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{oneshot, Mutex};
use tokio::time::{timeout, Duration};

/// Requests that outlive this are treated as failed. A full sync of ~2,900
/// reminders runs on the sidecar's worker thread and reports progress through
/// events, so no single call should ever be slow enough to hit this.
const CALL_TIMEOUT: Duration = Duration::from_secs(90);

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>;

pub struct Sidecar {
    stdin: Mutex<ChildStdin>,
    pending: Pending,
    next_id: AtomicU64,
    _child: Mutex<Child>,
}

impl Sidecar {
    /// Launch the sidecar and start pumping its output.
    pub async fn spawn(app: AppHandle, program: PathBuf, args: Vec<String>) -> Result<Arc<Self>> {
        let mut cmd = Command::new(&program);
        cmd.args(&args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        // Without this a console window flashes up on every launch on Windows.
        // tokio's Command exposes creation_flags directly on this platform.
        #[cfg(windows)]
        {
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd
            .spawn()
            .with_context(|| format!("failed to start sidecar at {}", program.display()))?;

        let stdin = child.stdin.take().ok_or_else(|| anyhow!("no sidecar stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| anyhow!("no sidecar stdout"))?;
        let stderr = child.stderr.take().ok_or_else(|| anyhow!("no sidecar stderr"))?;

        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));

        // stdout: responses and events.
        {
            let pending = pending.clone();
            let app = app.clone();
            tokio::spawn(async move {
                let mut lines = BufReader::new(stdout).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    if line.trim().is_empty() {
                        continue;
                    }
                    let msg: Value = match serde_json::from_str(&line) {
                        Ok(v) => v,
                        Err(e) => {
                            log::warn!("sidecar sent unparseable line: {e}: {line}");
                            continue;
                        }
                    };

                    if let Some(event) = msg.get("event").and_then(|v| v.as_str()) {
                        let data = msg.get("data").cloned().unwrap_or(Value::Null);
                        let _ = app.emit(&format!("sidecar://{event}"), data);
                        continue;
                    }

                    let Some(id) = msg.get("id").and_then(|v| v.as_u64()) else {
                        continue;
                    };
                    let tx = { pending.lock().await.remove(&id) };
                    if let Some(tx) = tx {
                        let payload = if msg.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                            Ok(msg.get("result").cloned().unwrap_or(Value::Null))
                        } else {
                            Err(msg
                                .get("error")
                                .map(|e| e.to_string())
                                .unwrap_or_else(|| "unknown sidecar error".into()))
                        };
                        let _ = tx.send(payload);
                    }
                }
                log::error!("sidecar stdout closed; the child has exited");
                let _ = app.emit("sidecar://died", json!({}));
            });
        }

        // stderr: sidecar logging.
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                log::info!("[sidecar] {line}");
            }
        });

        Ok(Arc::new(Self {
            stdin: Mutex::new(stdin),
            pending,
            next_id: AtomicU64::new(1),
            _child: Mutex::new(child),
        }))
    }

    /// Issue a request and wait for its response.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);

        let line = serde_json::json!({ "id": id, "method": method, "params": params });
        let mut body = serde_json::to_string(&line).map_err(|e| e.to_string())?;
        body.push('\n');

        {
            let mut stdin = self.stdin.lock().await;
            if let Err(e) = stdin.write_all(body.as_bytes()).await {
                self.pending.lock().await.remove(&id);
                return Err(format!("sidecar write failed: {e}"));
            }
            if let Err(e) = stdin.flush().await {
                self.pending.lock().await.remove(&id);
                return Err(format!("sidecar flush failed: {e}"));
            }
        }

        match timeout(CALL_TIMEOUT, rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("sidecar dropped the response channel".into()),
            Err(_) => {
                self.pending.lock().await.remove(&id);
                Err(format!("sidecar timed out after {}s", CALL_TIMEOUT.as_secs()))
            }
        }
    }
}

#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

mod auth;
mod cache;
mod cloudkit;
mod error;
mod icloud;
mod model;
mod notifications;
mod secrets;
mod server;
mod sync;
mod timeutil;

use std::path::PathBuf;

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::sync::mpsc;

use server::Server;

const MAX_PROTOCOL_LINE_BYTES: usize = 1024 * 1024;

fn arguments() -> (PathBuf, Option<String>) {
    let mut args = std::env::args_os().skip(1);
    let mut data_dir = None;
    let mut apple_id = None;
    while let Some(arg) = args.next() {
        if arg == "--data-dir" {
            data_dir = args.next().map(PathBuf::from);
        } else if arg == "--apple-id" {
            apple_id = args.next().map(|v| v.to_string_lossy().into_owned());
        }
    }
    let fallback = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    (
        data_dir
            .or_else(|| std::env::var_os("REMINDERS_DATA_DIR").map(PathBuf::from))
            .unwrap_or_else(|| fallback.join("RemindersSync")),
        apple_id.or_else(|| std::env::var("REMINDERS_APPLE_ID").ok()),
    )
}

#[cfg(windows)]
#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("reminders-sidecar: {error}");
        std::process::exit(1);
    }
}

#[cfg(not(windows))]
fn main() {
    eprintln!("reminders-sidecar supports Windows only");
    std::process::exit(1);
}

#[cfg(windows)]
async fn run() -> error::Result<()> {
    let (data_dir, apple_id) = arguments();
    let (tx, mut rx) = mpsc::unbounded_channel::<Value>();
    tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(message) = rx.recv().await {
            let Ok(mut line) = serde_json::to_vec(&message) else {
                continue;
            };
            line.push(b'\n');
            if stdout.write_all(&line).await.is_err() {
                break;
            }
            let _ = stdout.flush().await;
        }
    });
    let server = Server::open(&data_dir, apple_id, tx.clone())?;
    server.start().await;
    // Cap the reader itself rather than checking the length after the fact:
    // `next_line` would otherwise buffer an unbounded line before we could
    // reject it. One byte of headroom lets us detect an over-long line.
    let stdin = tokio::io::stdin().take((MAX_PROTOCOL_LINE_BYTES + 1) as u64);
    let mut lines = BufReader::new(stdin).lines();
    while let Some(line) = lines.next_line().await.map_err(|e| {
        error::AppError::internal("Could not read the command stream", e.to_string())
    })? {
        if line.trim().is_empty() {
            continue;
        }
        if line.len() > MAX_PROTOCOL_LINE_BYTES {
            let _=tx.send(json!({"id":Value::Null,"ok":false,"error":{"code":"BAD_REQUEST","message":"request is too large","detail":""}}));
            break;
        }
        let request: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => {
                let _=tx.send(json!({"id":Value::Null,"ok":false,"error":{"code":"BAD_REQUEST","message":"invalid JSON","detail":""}}));
                continue;
            }
        };
        let id = request.get("id").cloned().unwrap_or(Value::Null);
        let method = request
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        let params = request.get("params").cloned().unwrap_or_else(|| json!({}));

        // `shutdown` is answered inline so the reply is ordered before the
        // loop exits. Everything else runs on its own task: the protocol is
        // id-multiplexed and the client issues overlapping calls, so awaiting
        // dispatch here would let one slow request (a sign-in can occupy the
        // full 60s HTTP timeout) stall every later one, including `ping`.
        if method == "shutdown" {
            let _ = tx.send(json!({"id":id,"ok":true,"result":{"bye":true}}));
            break;
        }
        let server = server.clone();
        let tx = tx.clone();
        tokio::spawn(async move {
            let response = match server.dispatch(&method, params).await {
                Ok(mut value) => {
                    if let Some(object) = value.as_object_mut() {
                        object.remove("shutdown");
                    }
                    json!({"id":id,"ok":true,"result":value})
                }
                Err(error) => {
                    let code = error.code();
                    let mut body = match serde_json::to_value(error.body()) {
                        Ok(body) => body,
                        Err(_) => json!({"code":"INTERNAL","message":"internal error","detail":""}),
                    };
                    if code == "BAD_REQUEST"
                        && body.get("detail").and_then(Value::as_str) == Some("NO_METHOD")
                    {
                        body["code"] = json!("NO_METHOD");
                        body["detail"] = json!("");
                    }
                    json!({"id":id,"ok":false,"error":body})
                }
            };
            let _ = tx.send(response);
        });
    }
    Ok(())
}

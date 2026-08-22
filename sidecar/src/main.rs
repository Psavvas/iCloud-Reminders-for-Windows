// Everything below `main` is Windows-only, so on other platforms the whole crate
// is unreachable and `dead_code` fires on all of it.
//
// `unused_imports` is deliberately NOT exempted. CI lints on Windows with
// -D warnings, so anything hidden here is only discovered after a push -- which
// is exactly how an unused test import got through. Imports that genuinely only
// serve Windows-only code carry their own `#[cfg(windows)]` instead, which
// keeps the lint honest on both platforms.
#![cfg_attr(not(windows), allow(dead_code))]

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
use std::time::Duration;

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, BufReader};
#[cfg(windows)]
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;
use tokio::task::JoinSet;

use server::Server;

const MAX_PROTOCOL_LINE_BYTES: usize = 1024 * 1024;
/// How long to let in-flight requests finish, and their replies flush, once
/// stdin closes. Bounded so a hung request cannot keep the process alive.
const SHUTDOWN_DRAIN: Duration = Duration::from_secs(10);

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
    let writer = tokio::spawn(async move {
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
    serve(tokio::io::stdin(), server, tx).await?;
    // Flush anything the drain queued before the runtime goes away.
    let _ = tokio::time::timeout(SHUTDOWN_DRAIN, writer).await;
    Ok(())
}

/// Reads newline-delimited requests, dispatches them, and writes replies to
/// `tx`. Split out from `run` so the protocol loop can be tested without a
/// real stdin; `run` supplies the process's stdin.
async fn serve<R>(
    reader: R,
    server: std::sync::Arc<Server>,
    tx: mpsc::UnboundedSender<Value>,
) -> error::Result<()>
where
    R: tokio::io::AsyncRead + Unpin,
{
    let mut reader = BufReader::new(reader);
    let mut buffer = Vec::new();
    let mut inflight = JoinSet::new();
    loop {
        let outcome = read_bounded_line(&mut reader, &mut buffer).await.map_err(|e| {
            error::AppError::internal("Could not read the command stream", e.to_string())
        })?;
        let line = match outcome {
            LineOutcome::Eof => break,
            LineOutcome::TooLong => {
                // One request being too big is not a reason to stop serving the
                // ones behind it.
                let _=tx.send(json!({"id":Value::Null,"ok":false,"error":{"code":"BAD_REQUEST","message":"request is too large","detail":""}}));
                continue;
            }
            LineOutcome::Line => String::from_utf8_lossy(&buffer).into_owned(),
        };
        if line.trim().is_empty() {
            continue;
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
        let task_tx = tx.clone();
        inflight.spawn(async move {
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
            let _ = task_tx.send(response);
        });
        // Reap finished work so the set does not grow for the process lifetime.
        while inflight.try_join_next().is_some() {}
    }

    // stdin has closed (or `shutdown` was requested). Requests dispatched onto
    // their own tasks may still be mid-flight, and returning here would drop
    // them along with their replies -- which is what made the build script's
    // `ping` smoke test flaky, since it pipes one line and immediately hits
    // EOF. Let in-flight work finish before closing the channel.
    let _ = tokio::time::timeout(SHUTDOWN_DRAIN, async {
        while inflight.join_next().await.is_some() {}
    })
    .await;
    drop(tx);
    Ok(())
}

enum LineOutcome {
    Line,
    TooLong,
    Eof,
}

/// Read one newline-terminated request into `buffer`.
///
/// The cap is per line, and enforced as the line streams in: an over-long
/// request is discarded up to its newline without ever being held in memory,
/// and the ones behind it are still served.
///
/// This replaced `reader.take(MAX + 1)`, which reads as a line cap but is a cap
/// on the *whole stream* -- so the loop stopped reading for good once a session
/// had sent a megabyte in total, and the app reported the sync service as
/// stopped. The UI polls status every fifteen seconds and this app lives in the
/// tray for weeks, so that is a few days of uptime, not a theoretical limit.
async fn read_bounded_line<R>(reader: &mut R, buffer: &mut Vec<u8>) -> std::io::Result<LineOutcome>
where
    R: tokio::io::AsyncBufRead + Unpin,
{
    buffer.clear();
    let mut too_long = false;
    loop {
        let available = reader.fill_buf().await?;
        if available.is_empty() {
            return Ok(if too_long {
                LineOutcome::TooLong
            } else if buffer.is_empty() {
                LineOutcome::Eof
            } else {
                // A trailing request with no newline is still a request.
                LineOutcome::Line
            });
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.unwrap_or(available.len());
        if !too_long {
            if buffer.len() + take > MAX_PROTOCOL_LINE_BYTES {
                too_long = true;
                buffer.clear();
            } else {
                buffer.extend_from_slice(&available[..take]);
            }
        }
        reader.consume(take + usize::from(newline.is_some()));
        if newline.is_some() {
            return Ok(if too_long {
                LineOutcome::TooLong
            } else {
                LineOutcome::Line
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Server, serve};
    use serde_json::{Value, json};
    use tokio::sync::mpsc;

    /// Runs the protocol loop over a fixed script of request lines and returns
    /// everything written back, in order.
    async fn exchange(script: &str) -> Vec<Value> {
        let dir = tempfile::tempdir().expect("temp dir");
        let (tx, mut rx) = mpsc::unbounded_channel();
        let server = Server::open(dir.path(), Some("someone@example.com".into()), tx.clone())
            .expect("open server");
        serve(script.as_bytes(), server, tx)
            .await
            .expect("the protocol loop should not fail");

        let mut messages = Vec::new();
        while let Ok(message) = rx.try_recv() {
            messages.push(message);
        }
        messages
    }

    fn replies(messages: &[Value]) -> Vec<&Value> {
        messages.iter().filter(|m| m.get("id").is_some()).collect()
    }

    /// Regression test. Dispatch runs on its own task, so a request piped in
    /// immediately before EOF used to be dropped together with its reply --
    /// the build script's one-line smoke test hit this. The loop now drains
    /// in-flight work before returning.
    #[tokio::test]
    async fn a_request_immediately_followed_by_eof_still_gets_its_reply() {
        let messages = exchange("{\"id\":1,\"method\":\"ping\",\"params\":{}}\n").await;
        let replies = replies(&messages);
        assert_eq!(replies.len(), 1, "expected exactly one reply: {messages:?}");
        assert_eq!(replies[0]["id"], json!(1));
        assert_eq!(replies[0]["ok"], json!(true));
        assert_eq!(replies[0]["result"]["pong"], json!(true));
    }

    #[tokio::test]
    async fn every_request_in_a_batch_is_answered_exactly_once() {
        let script = (1..=8)
            .map(|id| format!("{{\"id\":{id},\"method\":\"ping\",\"params\":{{}}}}"))
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        let messages = exchange(&script).await;
        let mut ids: Vec<i64> = replies(&messages)
            .iter()
            .filter_map(|reply| reply["id"].as_i64())
            .collect();
        ids.sort_unstable();
        assert_eq!(ids, (1..=8).collect::<Vec<_>>());
    }

    #[tokio::test]
    async fn shutdown_is_acknowledged_and_stops_reading() {
        let messages = exchange(concat!(
            "{\"id\":1,\"method\":\"shutdown\",\"params\":{}}\n",
            "{\"id\":2,\"method\":\"ping\",\"params\":{}}\n"
        ))
        .await;
        let replies = replies(&messages);
        assert_eq!(replies.len(), 1, "nothing after shutdown should be served");
        assert_eq!(replies[0]["id"], json!(1));
        assert_eq!(replies[0]["result"]["bye"], json!(true));
    }

    #[tokio::test]
    async fn malformed_and_blank_lines_do_not_stop_the_loop() {
        let messages = exchange(concat!(
            "\n",
            "not json at all\n",
            "{\"id\":7,\"method\":\"ping\",\"params\":{}}\n"
        ))
        .await;
        let replies = replies(&messages);
        // The bad line answers with a null id, then the good one is served.
        assert!(
            replies.iter().any(|r| r["id"] == json!(7) && r["ok"] == json!(true)),
            "a later valid request must still be served: {messages:?}"
        );
        assert!(
            replies
                .iter()
                .any(|r| r["id"] == Value::Null && r["error"]["code"] == json!("BAD_REQUEST")),
            "invalid JSON should be reported: {messages:?}"
        );
    }

    #[tokio::test]
    async fn an_unknown_method_is_reported_as_no_method_on_the_wire() {
        let messages = exchange("{\"id\":3,\"method\":\"nope\",\"params\":{}}\n").await;
        let replies = replies(&messages);
        assert_eq!(replies[0]["ok"], json!(false));
        assert_eq!(replies[0]["error"]["code"], json!("NO_METHOD"));
        assert_eq!(replies[0]["error"]["detail"], json!(""));
    }

    /// Regression test. The cap was applied with `reader.take(..)`, which
    /// limits the *whole stream* rather than one line -- so the sidecar stopped
    /// reading for good once a session had sent a megabyte in total, and the app
    /// reported the sync service as stopped. That is days of uptime, not
    /// minutes: the UI polls status every fifteen seconds and this thing lives
    /// in the tray for weeks.
    #[tokio::test]
    async fn the_size_cap_applies_per_line_not_to_the_whole_session() {
        let pad = "x".repeat(100 * 1024);
        let script: String = (1..=12)
            .map(|id| format!("{{\"id\":{id},\"method\":\"ping\",\"params\":{{\"pad\":\"{pad}\"}}}}\n"))
            .collect();
        assert!(
            script.len() > super::MAX_PROTOCOL_LINE_BYTES,
            "the script has to cross the cap for this to prove anything"
        );

        let messages = exchange(&script).await;
        let mut ids: Vec<i64> = replies(&messages)
            .iter()
            .filter_map(|reply| reply["id"].as_i64())
            .collect();
        ids.sort_unstable();
        assert_eq!(ids, (1..=12).collect::<Vec<_>>());
    }

    /// One request being too big is not a reason to stop serving the ones after
    /// it.
    #[tokio::test]
    async fn a_refused_line_does_not_end_the_session() {
        let huge = "x".repeat(super::MAX_PROTOCOL_LINE_BYTES + 512);
        let messages = exchange(&format!(
            "{{\"id\":4,\"method\":\"ping\",\"pad\":\"{huge}\"}}\n{{\"id\":5,\"method\":\"ping\",\"params\":{{}}}}\n"
        ))
        .await;
        let replies = replies(&messages);
        assert!(
            replies.iter().any(|r| r["error"]["message"] == json!("request is too large")),
            "the oversized line should be refused: {messages:?}"
        );
        assert!(
            replies.iter().any(|r| r["id"] == json!(5) && r["ok"] == json!(true)),
            "the request after it must still be served: {messages:?}"
        );
    }

    #[tokio::test]
    async fn an_oversized_line_is_refused_without_buffering_it() {
        let huge = "x".repeat(super::MAX_PROTOCOL_LINE_BYTES + 512);
        let messages = exchange(&format!("{{\"id\":4,\"method\":\"ping\",\"pad\":\"{huge}\"}}\n")).await;
        let replies = replies(&messages);
        assert_eq!(replies.len(), 1);
        assert_eq!(replies[0]["error"]["code"], json!("BAD_REQUEST"));
        assert_eq!(replies[0]["error"]["message"], json!("request is too large"));
    }
}

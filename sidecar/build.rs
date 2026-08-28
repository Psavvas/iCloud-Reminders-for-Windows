//! Embeds the commit the sidecar was built from.
//!
//! Several rounds of live debugging were spent unable to tell which build had
//! produced a log. The binary now says so itself.

use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=../.git/HEAD");
    let revision = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .filter(|out| out.status.success())
        .and_then(|out| String::from_utf8(out.stdout).ok())
        .map(|text| text.trim().to_owned())
        .filter(|text| !text.is_empty())
        .unwrap_or_else(|| "unknown".to_owned());
    println!("cargo:rustc-env=REMINDERS_BUILD_REVISION={revision}");
}

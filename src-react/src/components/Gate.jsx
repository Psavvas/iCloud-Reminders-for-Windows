import { useState } from "react";
import { call, invoke, parseError } from "../bridge.js";
import AppMark from "./AppMark.jsx";

export default function Gate({
  step, setStep, error, setError, sidecarDetail, setSidecarDetail, onSignedIn,
}) {
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busyText, setBusyText] = useState("Connecting…");

  const signIn = async (acceptTerms = false) => {
    setError("");
    setBusyText("Signing in…");
    setStep("loading");
    try {
      await call("login", {
        apple_id: appleId.trim(),
        password,
        accept_terms: acceptTerms,
      });
      onSignedIn();
    } catch (e) {
      const { code: c, message, detail } = parseError(e);
      if (c === "2FA_REQUIRED") {
        try { await call("request_2fa"); } catch { /* may already be sent */ }
        setStep("2fa");
      } else if (c === "TERMS_REQUIRED") {
        setStep("terms");
      } else if (c === "SIDECAR_DOWN") {
        setSidecarDetail(detail || message);
        setStep("sidecar");
      } else {
        setStep("login");
        setError(message);
      }
    }
  };

  return (
    <div className="gate">
      <div className="gate-card">
        <div className="gate-mark"><AppMark /></div>
        <h1>iCloud Reminders</h1>
        <p className="gate-sub">Your reminders, on Windows.</p>

        {step === "loading" && (
          <div className="gate-step">
            <div className="spinner" />
            <p className="hint centered">{busyText}</p>
          </div>
        )}

        {step === "login" && (
          <form
            className="gate-step"
            onSubmit={(e) => {
              e.preventDefault();
              signIn();
            }}
          >
            <div className="field">
              <label htmlFor="apple-id">Apple ID</label>
              <input
                id="apple-id"
                type="email"
                autoComplete="username"
                placeholder="you@icloud.com"
                value={appleId}
                onChange={(e) => setAppleId(e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <button type="submit" className="primary">Sign In</button>
            <p className="hint centered lock">
              Stored in Windows Credential Manager. Never written to disk in the
              clear.
            </p>
          </form>
        )}

        {step === "2fa" && (
          <form
            className="gate-step"
            onSubmit={async (e) => {
              e.preventDefault();
              setError("");
              try {
                await call("submit_2fa", { code: code.trim() });
                onSignedIn();
              } catch (err) {
                setError(parseError(err).message);
              }
            }}
          >
            <p className="hint centered">
              Approve the prompt on your iPhone, then enter the six digits.
            </p>
            <input
              className="code-input"
              inputMode="numeric"
              maxLength={6}
              placeholder="······"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              autoFocus
            />
            <button type="submit" className="primary">Verify</button>
            <button
              type="button"
              className="linkish"
              onClick={() => call("request_2fa").catch(() => {})}
            >
              Send a new code
            </button>
          </form>
        )}

        {step === "terms" && (
          <div className="gate-step">
            <p className="warn centered strong">Updated iCloud terms</p>
            <p className="hint centered">
              Apple needs you to accept them before syncing. You can also accept
              at icloud.com.
            </p>
            <button className="primary" onClick={() => signIn(true)}>
              Accept and Continue
            </button>
          </div>
        )}

        {step === "sidecar" && (
          <div className="gate-step">
            <p className="warn centered strong">The sync service isn't running.</p>
            <p className="hint centered">
              It's a separate executable built by{" "}
              <code>scripts\build-sidecar.ps1</code>. Run that, then press Retry.
            </p>
            <pre className="detail-log">{sidecarDetail}</pre>
            <button
              className="primary"
              onClick={async () => {
                setBusyText("Starting the sync service…");
                setStep("loading");
                try {
                  await invoke("restart_sidecar");
                  onSignedIn();
                } catch (e) {
                  setSidecarDetail(String(e));
                  setStep("sidecar");
                }
              }}
            >
              Retry
            </button>
          </div>
        )}

        {error && <p className="error centered">{error}</p>}
      </div>
    </div>
  );
}

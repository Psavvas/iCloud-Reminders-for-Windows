import { useState } from "react";
import { call, invoke, parseError } from "../bridge.js";
import AppMark from "./AppMark.jsx";

/**
 * What to tell someone waiting on a code, given how Apple sent it.
 *
 * Not decoration. Apple picks the route -- a prompt on a trusted device, a text,
 * or a hardware key it will not let us prompt for -- and "approve the prompt on
 * your iPhone" is useless advice to someone whose code came by SMS. They read
 * it, look at the wrong screen, and conclude the app is broken.
 */
function deliveryHint(twoFactor) {
  const { method, notice, sent } = twoFactor || {};
  if (notice) return notice;
  if (method === "sms") return "We texted you a code. Enter the six digits.";
  if (method === "security_key")
    return "This Apple ID verifies with a hardware security key. Sign in at icloud.com once to trust this PC, then try again.";
  if (method === "trusted_device")
    return "Approve the prompt on your iPhone, then enter the six digits.";
  if (sent === false)
    return "Apple didn't confirm it sent a code. Try Send a new code.";
  return "Enter the six-digit code Apple sent you.";
}

export default function Gate({
  step, setStep, error, setError, sidecarDetail, setSidecarDetail, onSignedIn,
  twoFactor, setTwoFactor,
}) {
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busyText, setBusyText] = useState("Connecting…");
  const [resending, setResending] = useState(false);

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
      const { code: c, message, detail, data } = parseError(e);
      if (c === "2FA_REQUIRED") {
        // Deliberately *not* asking for another code here. The sidecar armed
        // the challenge as it raised this, and asking again makes Apple mint a
        // new code and retire the one already on its way -- so the message the
        // user is reading goes stale as they read it, and every code they type
        // comes back invalid.
        setTwoFactor(data || {});
        setCode("");
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

  const resend = async () => {
    setError("");
    setResending(true);
    try {
      const info = await call("request_2fa");
      setTwoFactor(info || {});
      setCode("");
      if (info && info.sent === false) {
        setError(
          info.error
            ? "Apple wouldn't send a code: " + info.error
            : "Apple didn't send a code. Check your devices are online."
        );
      }
    } catch (e) {
      // Swallowing this is what left people entering codes against a challenge
      // that had never been set up.
      setError(parseError(e).message);
    } finally {
      setResending(false);
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

        {/* Signed in on a previous run. The sidecar is re-establishing the
            session from the saved credential and trust token, which needs
            nothing from the user -- so don't ask them for anything. */}
        {step === "restoring" && (
          <div className="gate-step">
            <div className="spinner" />
            <p className="hint centered">Signing you back in…</p>
            <button className="linkish" onClick={() => setStep("login")}>
              Use a different account
            </button>
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
                await call("submit_2fa", { code });
                onSignedIn();
              } catch (err) {
                const { message, data } = parseError(err);
                // A rejection can carry a *replacement* challenge: Apple closes
                // the verification session after one verdict, so the sidecar
                // arms a new one rather than leaving the user retyping a code
                // that now has nothing to check it against.
                if (data && data.method) setTwoFactor(data);
                setCode("");
                setError(message);
              }
            }}
          >
            <p className="hint centered">{deliveryHint(twoFactor)}</p>
            <input
              className="code-input"
              inputMode="numeric"
              maxLength={6}
              placeholder="······"
              autoComplete="one-time-code"
              value={code}
              // Apple's mail and the Windows autofill both hand over "123 456".
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              required
              autoFocus
            />
            <button type="submit" className="primary">Verify</button>
            <button
              type="button"
              className="linkish"
              onClick={resend}
              disabled={resending}
            >
              {resending ? "Sending…" : "Send a new code"}
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

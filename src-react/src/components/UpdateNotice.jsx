import { useState } from "react";
import { invoke } from "../bridge.js";

/**
 * "A new version is ready" — and nothing happens until you say so.
 *
 * The app lives in the tray for weeks at a time, so installing on its own would
 * mean restarting underneath someone mid-edit. Restarting is the user's call;
 * the notice just waits, like AuthNotice, until it is acted on.
 */
export default function UpdateNotice({ version, onError }) {
  const [busy, setBusy] = useState(false);

  const install = async () => {
    setBusy(true);
    try {
      // On success the process is replaced, so this never resolves.
      await invoke("install_update");
    } catch (e) {
      setBusy(false);
      onError(String(e && e.message ? e.message : e));
    }
  };

  return (
    <div className="update-notice" role="status">
      <span className="update-notice-dot" aria-hidden="true" />
      <span className="update-notice-text">
        <strong>Version {version} is ready.</strong> Restarting takes a few
        seconds. Nothing syncing is lost — anything queued goes up afterwards.
      </span>
      <button className="primary small" onClick={install} disabled={busy}>
        {busy ? "Installing…" : "Restart to update"}
      </button>
    </div>
  );
}

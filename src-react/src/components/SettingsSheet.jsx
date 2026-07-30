import { useEffect, useState } from "react";
import Sheet from "./Sheet.jsx";
import { call, invoke } from "../bridge.js";

function Switch({ label, checked, onChange, disabled }) {
  return (
    <label className="switch-row">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
    </label>
  );
}

export default function SettingsSheet({
  settings, setSettings, lists, appleId, onClose, onFullSync, onSignOut, onThemeChange,
}) {
  const [autostart, setAutostart] = useState(false);
  const [autostartNote, setAutostartNote] = useState("");

  useEffect(() => {
    invoke("get_autostart")
      .then(setAutostart)
      .catch((e) => setAutostartNote("Couldn't read the startup setting: " + e));
  }, []);

  const save = async (patch) => {
    const next = await call("set_settings", patch);
    setSettings(next);
    if ("theme" in patch) onThemeChange(next.theme);
  };

  return (
    <Sheet onClose={onClose} className="wide">
      <h3>Settings</h3>

      <div className="settings-group">
        <div className="section-label">Appearance</div>
        <div className="setting-row">
          <label htmlFor="s-theme">Theme</label>
          <select
            id="s-theme"
            value={settings.theme || "system"}
            onChange={(e) => save({ theme: e.target.value })}
          >
            <option value="system">Match Windows</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
      </div>

      <div className="settings-group">
        <div className="section-label">Sync</div>
        <div className="setting-row">
          <label htmlFor="s-sync">Sync every</label>
          <select
            id="s-sync"
            value={String(settings.sync_minutes || 10)}
            onChange={(e) => save({ sync_minutes: Number(e.target.value) })}
          >
            <option value="5">5 minutes</option>
            <option value="10">10 minutes</option>
            <option value="15">15 minutes</option>
          </select>
        </div>
        <div className="setting-row">
          <label htmlFor="s-default">New reminders go to</label>
          <select
            id="s-default"
            value={settings.default_list_id || ""}
            onChange={(e) => save({ default_list_id: e.target.value || null })}
          >
            <option value="">Inbox (default)</option>
            {lists.filter((l) => !l.is_group).map((l) => (
              <option key={l.id} value={l.id}>
                {l.title}
              </option>
            ))}
          </select>
        </div>
        <button className="ghost wide-btn" onClick={onFullSync}>
          Re-download everything
        </button>
      </div>

      <div className="settings-group">
        <div className="section-label">Notifications</div>
        <Switch
          label="Show due-date notifications"
          checked={settings.notifications_enabled !== false}
          onChange={(v) => save({ notifications_enabled: v })}
        />
        <div className="setting-row">
          <label htmlFor="s-stale">Missed while asleep</label>
          <select
            id="s-stale"
            value={String(settings.stale_after_minutes || 60)}
            onChange={(e) => save({ stale_after_minutes: Number(e.target.value) })}
          >
            <option value="30">Summarise after 30 min</option>
            <option value="60">Summarise after 1 hour</option>
            <option value="240">Summarise after 4 hours</option>
          </select>
        </div>
        <p className="hint tiny">
          Anything overdue by more than this collapses into one summary instead
          of a toast each, so waking the machine doesn't flood the tray.
        </p>
      </div>

      <div className="settings-group">
        <div className="section-label">Windows</div>
        <Switch
          label="Start with Windows"
          checked={autostart}
          disabled={!!autostartNote}
          onChange={async (v) => {
            try {
              setAutostart(await invoke("set_autostart", { enabled: v }));
            } catch (err) {
              setAutostartNote("Couldn't change it: " + err);
            }
          }}
        />
        {autostartNote && <p className="hint tiny">{autostartNote}</p>}
      </div>

      <div className="settings-group">
        <div className="section-label">Account</div>
        <div className="setting-row static-row">
          <span className="muted-text">{appleId || "Signed in"}</span>
          <button className="danger small" onClick={onSignOut}>
            Sign Out
          </button>
        </div>
      </div>

      <div className="sheet-actions">
        <button className="primary" onClick={onClose}>
          Done
        </button>
      </div>
    </Sheet>
  );
}

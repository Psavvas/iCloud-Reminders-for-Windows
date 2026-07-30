import { useEffect, useState } from "react";
import { call, invoke } from "../bridge.js";
import AppMark from "./AppMark.jsx";

const STEPS = 4;

export default function Onboarding({ settings, setSettings, syncLine, counts, lists, onDone }) {
  const [step, setStep] = useState(0);
  const [autostart, setAutostart] = useState(false);
  const [autostartOk, setAutostartOk] = useState(true);

  useEffect(() => {
    invoke("get_autostart").then(setAutostart).catch(() => setAutostartOk(false));
  }, []);

  const save = async (patch) => setSettings(await call("set_settings", patch));
  const finish = async () => {
    await save({ onboarded: true });
    onDone();
  };

  return (
    <div className="onboard">
      <div className="onboard-card">
        <div className="onboard-steps" key={step}>
          {step === 0 && (
            <section className="onboard-step">
              <div className="onboard-art">
                <AppMark size={64} />
              </div>
              <h2>Welcome</h2>
              <p>
                Your iCloud reminders, on Windows — with due-date notifications
                Apple doesn't give you here.
              </p>
              <p className="hint">
                {syncLine ||
                  (counts.all
                    ? `${counts.all} reminders across ${lists.length} lists — ready.`
                    : "Downloading your reminders…")}
              </p>
            </section>
          )}

          {step === 1 && (
            <section className="onboard-step">
              <h2>It stays out of the way</h2>
              <p>
                Closing the window hides it to the tray instead of quitting.
                That's deliberate — the scheduler has to keep running for a
                due-date notification to ever fire.
              </p>
              <label className="switch-row">
                <span>Start with Windows</span>
                <input
                  type="checkbox"
                  checked={autostart}
                  disabled={!autostartOk}
                  onChange={async (e) => {
                    try {
                      setAutostart(
                        await invoke("set_autostart", { enabled: e.target.checked })
                      );
                    } catch {
                      setAutostartOk(false);
                    }
                  }}
                />
              </label>
              <label className="switch-row">
                <span>Show due-date notifications</span>
                <input
                  type="checkbox"
                  checked={settings.notifications_enabled !== false}
                  onChange={(e) => save({ notifications_enabled: e.target.checked })}
                />
              </label>
              <p className="hint tiny">Quit properly from the tray icon.</p>
            </section>
          )}

          {step === 2 && (
            <section className="onboard-step">
              <h2>Two things Apple won't allow</h2>
              <p>
                Worth knowing now rather than discovering later. Both were
                tested against a real account.
              </p>
              <ul className="onboard-list">
                <li>
                  <strong>Tags are read-only.</strong> They show up and you can
                  filter by them, but tags created here never render in Apple's
                  app, so this app doesn't pretend to write them.
                </li>
                <li>
                  <strong>Lists can't be created.</strong> Make them on your
                  phone and they'll appear here on the next sync.
                </li>
              </ul>
              <p className="hint tiny">
                Everything else — reminders, due dates, priorities, notes — is
                fully read/write.
              </p>
            </section>
          )}

          {step === 3 && (
            <section className="onboard-step">
              <h2>You're set</h2>
              <p>A few things worth knowing:</p>
              <ul className="onboard-list compact">
                <li><kbd>Ctrl</kbd> + <kbd>N</kbd> — new reminder</li>
                <li><kbd>Ctrl</kbd> + <kbd>F</kbd> — search, this list or everywhere</li>
                <li><kbd>Ctrl</kbd> + <kbd>P</kbd> — print the current list</li>
                <li><kbd>Ctrl</kbd> + <kbd>,</kbd> — settings</li>
              </ul>
              <p className="hint tiny">
                Each list remembers its own sort order, from the button beside
                search.
              </p>
            </section>
          )}
        </div>

        <div className="onboard-foot">
          <div className="dots">
            {Array.from({ length: STEPS }, (_, i) => (
              <span key={i} className={`dot-pip${i === step ? " on" : ""}`} />
            ))}
          </div>
          <div className="onboard-actions">
            {step < STEPS - 1 && (
              <button className="linkish inline" onClick={finish}>
                Skip
              </button>
            )}
            <button
              className="primary"
              onClick={() => (step === STEPS - 1 ? finish() : setStep(step + 1))}
            >
              {step === STEPS - 1 ? "Get Started" : "Continue"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

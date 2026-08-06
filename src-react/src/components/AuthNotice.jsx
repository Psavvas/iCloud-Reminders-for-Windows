/**
 * The one message that must not disappear.
 *
 * An expired session used to be announced with a toast. Toasts are a single
 * slot, so the next one -- "Sync failed", "Sync service reconnected", anything
 * -- replaced it, and a conflict prompt hid it outright. What was left was an
 * app showing cached reminders, a signed-in account in Settings, and no working
 * sync: it looked like nothing was wrong.
 *
 * So this is deliberately not a toast. It renders above the toast slot, has no
 * timeout, and cannot be dismissed -- the only way to clear it is to sign in
 * again, which is the only thing that fixes the underlying state.
 */
export default function AuthNotice({ appleId, onSignIn }) {
  return (
    <div className="auth-notice" role="alert">
      <span className="auth-notice-dot" aria-hidden="true" />
      <span className="auth-notice-text">
        <strong>iCloud sign-in needed.</strong> Apple ended the session
        {appleId ? ` for ${appleId}` : ""}, so nothing is syncing. Your
        reminders are still here, and anything you change is queued and will go
        up once you sign in.
      </span>
      <button className="primary small" onClick={onSignIn}>
        Sign in
      </button>
    </div>
  );
}

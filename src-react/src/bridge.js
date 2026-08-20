/**
 * The one place that touches Tauri.
 *
 * Everything else imports from here, so the UI can also run in a plain browser
 * (screenshots, quick iteration) by loading a mock that sets window.__TAURI__
 * before this module is first used.
 */

const tauri = () => window.__TAURI__;

export const invoke = (cmd, args) => {
  const t = tauri();
  if (!t) return Promise.reject(new Error("Tauri bridge unavailable"));
  return t.core.invoke(cmd, args);
};

export const call = (method, params = {}) => invoke("call", { method, params });

export function listen(event, handler) {
  const t = tauri();
  if (!t) return () => {};
  let dispose = () => {};
  let cancelled = false;
  t.event.listen(event, handler).then((un) => {
    if (cancelled) un();
    else dispose = un;
  });
  return () => {
    cancelled = true;
    dispose();
  };
}

/**
 * Sidecar errors arrive as a JSON string; pull out the parts the UI acts on.
 *
 * `data` carries whatever structure the error needs beyond prose -- 2FA uses it
 * to say how the code was delivered, which decides whether the screen should
 * say "your iPhone" or "your texts".
 */
export function parseError(e) {
  try {
    const o = typeof e === "string" ? JSON.parse(e) : e;
    return {
      code: o.code || "ERROR",
      message: o.message || String(e),
      detail: o.detail || "",
      data: o.data || {},
    };
  } catch {
    return { code: "ERROR", message: String(e), detail: "", data: {} };
  }
}

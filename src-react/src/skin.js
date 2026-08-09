/**
 * Which of the two interfaces is live.
 *
 * The app ships two, not one with a theme switch: Apple's, which is what an
 * iCloud client is expected to look like, and a native Fluent one for people
 * who would rather it looked like the rest of Windows. They have separate
 * stylesheets and separate components for the three panes, and the point of
 * this module is that only one stylesheet is ever attached to the document --
 * so neither can inherit a rule from the other, and switching cannot leave a
 * stray Apple radius on a Fluent button.
 *
 * `?url` is what makes that possible: Vite emits each sheet as its own asset
 * and hands over the URL rather than injecting the CSS at import time, so
 * attaching one is this file's decision instead of a side effect of the bundle
 * loading.
 *
 * It has to be a <link> to a real file, not a <style> holding the text. Tauri
 * appends its own nonces to `style-src` when it compiles the CSP, and a
 * directive carrying a nonce makes `'unsafe-inline'` inert for style
 * *elements* -- so an injected <style> is dropped on the floor and the whole
 * app renders unstyled, while inline style attributes and everything else keep
 * working. That failure is invisible outside the packaged app: a browser
 * loading dist/ has no CSP at all, so it looks perfect right up until it ships.
 * A <link> at a same-origin URL needs only `style-src 'self'`, which is what
 * the plain Vite stylesheet relied on before there were two of them.
 */

import appleCss from "./styles.css?url";
import winuiCss from "./winui.css?url";

export const UI_STYLES = [
  { value: "apple", label: "Apple", hint: "The look of Reminders on a Mac or iPhone." },
  { value: "winui", label: "Windows", hint: "Fluent / WinUI, like the rest of Windows 11." },
];

/** Anything unrecognised -- an older settings file, a typo -- means Apple. */
export const normalizeUi = (value) => (value === "winui" ? "winui" : "apple");

const SHEETS = { apple: appleCss, winui: winuiCss };

/** Marks the one <link> this module owns, so it can find it again. */
const MARK = "data-skin";

/**
 * Attach one skin's stylesheet and detach the other's.
 *
 * The new sheet is added first and the old one removed only once it has
 * loaded. Editing the existing link's href instead would drop the current
 * sheet the instant it changed, leaving the whole window unstyled until the
 * next one arrives -- a full-page white flash on what is meant to be a
 * preference change.
 *
 * Also mirrored into localStorage, for the same reason the theme is: the
 * inline script in index.html paints the right background before the bundle
 * arrives, and it cannot wait on a round trip to the sidecar to find out which
 * one that is. Settings remains the source of truth; this is a head start.
 */
export function applyUiStyle(value) {
  const ui = normalizeUi(value);
  document.documentElement.dataset.ui = ui;

  try {
    localStorage.setItem("ui_style", ui);
  } catch {
    /* storage unavailable; the round trip still sets it, just later */
  }

  const current = document.head.querySelector(`link[${MARK}]`);
  if (current && current.getAttribute(MARK) === ui) return ui;

  const next = document.createElement("link");
  next.rel = "stylesheet";
  next.setAttribute(MARK, ui);
  next.setAttribute("href", SHEETS[ui]);
  // Also on error: a sheet that failed to load is not a reason to keep two
  // links in the document forever.
  const drop = () => current && current.remove();
  next.addEventListener("load", drop, { once: true });
  next.addEventListener("error", drop, { once: true });
  document.head.appendChild(next);

  return ui;
}

/** What the last run chose, for the first paint before settings have loaded. */
export function storedUiStyle() {
  try {
    return normalizeUi(localStorage.getItem("ui_style"));
  } catch {
    return "apple";
  }
}

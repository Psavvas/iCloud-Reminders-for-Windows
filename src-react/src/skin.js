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
 * `?inline` is what makes that possible: Vite hands the CSS over as a string
 * instead of injecting it at import time, so attaching it is this file's
 * decision rather than a side effect of the bundle loading.
 */

import appleCss from "./styles.css?inline";
import winuiCss from "./winui.css?inline";

export const UI_STYLES = [
  { value: "apple", label: "Apple", hint: "The look of Reminders on a Mac or iPhone." },
  { value: "winui", label: "Windows", hint: "Fluent / WinUI, like the rest of Windows 11." },
];

/** Anything unrecognised -- an older settings file, a typo -- means Apple. */
export const normalizeUi = (value) => (value === "winui" ? "winui" : "apple");

const SHEETS = { apple: appleCss, winui: winuiCss };

const STYLE_ID = "skin-css";

/**
 * Attach one skin's stylesheet and detach the other's.
 *
 * Also mirrored into localStorage, for the same reason the theme is: the
 * inline script in index.html paints the right background before the bundle
 * arrives, and it cannot wait on a round trip to the sidecar to find out which
 * one that is. Settings remains the source of truth; this is a head start.
 */
export function applyUiStyle(value) {
  const ui = normalizeUi(value);
  const root = document.documentElement;
  root.dataset.ui = ui;

  let tag = document.getElementById(STYLE_ID);
  if (!tag) {
    tag = document.createElement("style");
    tag.id = STYLE_ID;
    document.head.appendChild(tag);
  }
  const css = SHEETS[ui];
  if (tag.textContent !== css) tag.textContent = css;

  try {
    localStorage.setItem("ui_style", ui);
  } catch {
    /* storage unavailable; the round trip still sets it, just later */
  }
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

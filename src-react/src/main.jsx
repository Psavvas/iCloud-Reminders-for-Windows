import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { applyUiStyle, storedUiStyle } from "./skin.js";

// Neither stylesheet is imported for its side effect: skin.js owns which one is
// attached, and importing one here would leave it live under the other. This
// runs before the first render so nothing paints unstyled.
applyUiStyle(storedUiStyle());

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

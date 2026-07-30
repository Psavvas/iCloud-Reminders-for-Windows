import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Output goes to ../dist, which tauri.conf.json serves as frontendDist.
// Relative base: the app is loaded from a file:// URL inside the webview, so
// absolute asset paths would not resolve.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    // WebView2 on a supported Windows build is evergreen Chromium.
    target: "chrome110",
  },
  clearScreen: false,
  server: { port: 5173, strictPort: true },
});

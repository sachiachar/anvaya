import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the agents API runs on :8080. In production Firebase Hosting
// rewrites /api/** to the Cloud Run service, so the app always calls /api.
// `ws: true` also proxies the live-call WebSocket in development.
// Three entry points: the app (index.html, the design in src/v2/), the platform admin portal
// (admin.html), served from its own Firebase Hosting site, and v3.html, a separate trial of Saathi
// as a talking avatar (src/v3/), which the app doesn't link to.
export default defineConfig({
  plugins: [react()],
  build: { rollupOptions: { input: { app: "index.html", admin: "admin.html", v3: "v3.html" } } },
  server: { proxy: { "/api": { target: "http://localhost:8080", ws: true } } },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the agents API runs on :8080. In production Firebase Hosting
// rewrites /api/** to the Cloud Run service, so the app always calls /api.
// `ws: true` also proxies the live-call WebSocket in development.
// Three entry points: the app (index.html), the new design of the app (v2.html, served beside it at
// /v2.html), and the platform admin portal (admin.html), served from its own Firebase Hosting site.
export default defineConfig({
  plugins: [react()],
  build: { rollupOptions: { input: { app: "index.html", v2: "v2.html", admin: "admin.html" } } },
  server: { proxy: { "/api": { target: "http://localhost:8080", ws: true } } },
});

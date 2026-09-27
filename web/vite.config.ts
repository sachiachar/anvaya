import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the agents API runs on :8080. In production Firebase Hosting
// rewrites /api/** to the Cloud Run service, so the app always calls /api.
// `ws: true` also proxies the live-call WebSocket in development.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8080", ws: true } } },
});

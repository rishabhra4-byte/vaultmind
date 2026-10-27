import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    // Never inline assets as data: URIs. Fonts must be real files so the CSP can stay at
    // font-src 'self' (no data:), and hashed files get long-lived caching.
    assetsInlineLimit: 0,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});

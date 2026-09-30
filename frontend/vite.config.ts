import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the API runs separately (phonesystem serve on :8000);
// requests to /api are proxied there.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": process.env.PHONESYSTEM_API ?? "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
  },
});

/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // The dev API server; avoids CORS entirely in development.
      "/api": "http://localhost:8000",
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Existing VAYREN Python backend (if exposed via HTTP)
      "/api": "http://localhost:8000",
    },
  },
});

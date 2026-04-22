import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite proxies /api -> FastAPI on :8000 so the UI can call relative URLs
// in both dev and prod (when we eventually serve the static build from
// FastAPI itself). No CORS gymnastics, no env-sniffing in the client.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

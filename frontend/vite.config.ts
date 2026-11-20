import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발: http://127.0.0.1:5173 → /api 는 FastAPI(127.0.0.1:8000)로 전달
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
});

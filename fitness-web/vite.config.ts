import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 仅开发服务器使用，不通过 VITE_* 暴露到浏览器构建产物。
const apiTarget = loadEnv("development", ".", "FITNESS_").FITNESS_DEV_API_URL ?? "http://127.0.0.1:8090";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": apiTarget,
      "/health": apiTarget,
    },
  },
});

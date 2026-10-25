import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: { react: ["react", "react-dom"], flow: ["@xyflow/react"], term: ["@xterm/xterm", "@xterm/addon-fit"] },
      },
    },
  },
  server: { proxy: { "/api": "http://localhost:8080", "/ws": { target: "ws://localhost:8080", ws: true } } },
});

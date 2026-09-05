import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    outDir: fileURLToPath(
      new URL("../src/pdf_to_markdown/review_static", import.meta.url),
    ),
    emptyOutDir: true,
  },
});

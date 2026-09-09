import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Flowing webui：React 19 + TS + Vite 6 + Tailwind 4 + singlefile（技术栈直接
// 复用 kimi-code 的 web 前端 @moonshot-ai/vis-web 的构建组合，kimi-code
// @ a4a7df2 2026-09-10）。产物是单文件 index.html（../webui-dist/），由
// flowing.interfaces.web 随包发布并经 GET / 返回。
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: { outDir: "../flowing/interfaces/webui-dist", emptyOutDir: true },
});

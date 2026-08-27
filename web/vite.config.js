import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期 npm run dev 走 5173，/api 代理到 FastAPI(8000)；
// 生产 npm run build 后由 FastAPI 直接托管 dist，无需代理。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})

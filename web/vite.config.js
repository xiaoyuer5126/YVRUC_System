import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期 npm run dev 走 5173，/api 代理到 FastAPI(8000)；
// 生产 npm run build 出 dist，由 web 容器的 nginx 托管，/api 反代到 backend。

// 构建号：同一个值既编进 bundle（__BUILD_ID__），又单独落一份 version.json。
// 页面拿手里的号跟服务器上的比，就知道自己是不是旧版——见 src/version.js。
const BUILD_ID = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')

export default defineConfig({
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  plugins: [vue(), {
    name: 'rucva-build-id',
    generateBundle() {
      // 落在 dist 根下而不是 /assets/：那里是按内容哈希命名的永久缓存
      this.emitFile({ type: 'asset', fileName: 'version.json',
                      source: JSON.stringify({ build: BUILD_ID }) })
    },
  }],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})

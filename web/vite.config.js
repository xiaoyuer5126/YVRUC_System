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
    name: 'yvruc-build-id',
    generateBundle() {
      // 落在 dist 根下而不是 /assets/：那里是按内容哈希命名的永久缓存
      this.emitFile({ type: 'asset', fileName: 'version.json',
                      source: JSON.stringify({ build: BUILD_ID }) })
    },
  }],
  build: {
    rollupOptions: {
      output: {
        // 几个大依赖各自成一个文件，不跟页面代码编在一起。
        //
        // 不这样分：echarts 并进「数据总览」、zxing 并进「我的报名」、
        // element-plus 并进入口文件，页面代码改一行哈希就全变，浏览器得把
        // 一两兆重新下一遍。各自成包之后，这几个文件只在升级依赖时才变。
        manualChunks: {
          vue: ['vue', 'vue-router'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          echarts: ['echarts'],
          leaflet: ['leaflet'],
          zxing: ['@zxing/browser'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})

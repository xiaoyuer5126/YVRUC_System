import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'
import { watchVersion } from './version'
import './styles.css'

const app = createApp(App)
for (const [name, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, comp)
}
// Element Plus 自己的语言包（日期选择器、分页、表格空态）由 App.vue 里的
// el-config-provider 绑到当前界面语言上，切换语言时就地换掉，不必刷新页面
app.use(ElementPlus)
app.use(router)
app.mount('#app')
watchVersion()

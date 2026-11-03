import { computed, createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import * as icons from './icons'
import { elLocale } from './i18n'
import { router } from './router'
import { watchVersion } from './version'
import './styles.css'

const app = createApp(App)
// 图标在 icons.js 里逐个列出，那里写了为什么不整包注册
for (const [name, comp] of Object.entries(icons)) {
  app.component(name, comp)
}
// Element Plus 自己的语言包（日期选择器、分页、表格空态、确认框的「确定/取消」）
// 挂在应用这一层，并且传的是 computed：切换语言时就地换掉，不必刷新页面。
// ElMessageBox / ElMessage 是用函数调起、挂在应用上下文上的，只认应用这一层的
// 配置；要是只在 App.vue 里包一层 el-config-provider，它们拿不到切换后的语言。
app.use(ElementPlus, computed(() => ({ locale: elLocale.value })))
app.use(router)
app.mount('#app')
watchVersion()

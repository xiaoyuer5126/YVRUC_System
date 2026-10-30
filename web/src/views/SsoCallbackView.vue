<template>
  <div class="cb-wrap">
    <el-card class="cb-card">
      <div class="head">
        <div class="logo">志</div>
        <h2>{{ t('志愿服务管理平台') }}</h2>
      </div>

      <el-result v-if="error" icon="error" :title="t('登录未完成')" :sub-title="error">
        <template #extra>
          <el-button type="primary" @click="backToLogin">{{ t('重新登录') }}</el-button>
        </template>
      </el-result>

      <div v-else class="waiting">
        <el-icon class="is-loading" :size="26"><Loading /></el-icon>
        <p>{{ t('正在完成统一身份认证…') }}</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { api } from '../api'
import { setAuth } from '../store'
import { t } from '../i18n'

const route = useRoute()
const router = useRouter()
const error = ref('')

onMounted(async () => {
  // CAS 把 ?code=..&state=.. 追加在平台根地址上，路由守卫原样转到本页
  const code = String(route.query.code || '')
  const state = String(route.query.state || '')

  // 立刻把授权码从地址栏抹掉：它会进浏览器历史和 Referer
  router.replace({ path: '/sso-callback' })

  if (!code || !state) {
    error.value = t('回调地址里缺少 code 或 state，请从平台首页重新登录。')
    return
  }

  try {
    const d = await api.post('/api/auth/sso-callback', { code, state },
                             { skipErrorToast: true })
    setAuth(d.token, d.user)
    ElMessage.success(t('欢迎，{name}', { name: d.user.name }))
    // replace 而不是 push：带授权码的那一页不该留在前进/后退里。
    // next 是跳转前想去的页面（比如从签到二维码落地页被弹去登录的志愿者）。
    router.replace(d.next || '/')
  } catch (e) {
    error.value = e.message || t('统一身份认证未通过，请重试。')
  }
})

function backToLogin() { router.replace('/login') }
</script>

<style scoped>
.cb-wrap {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background:
    radial-gradient(1000px 500px at 15% -10%, rgba(174,11,42,.18), transparent),
    radial-gradient(900px 500px at 110% 110%, rgba(31,90,168,.12), transparent),
    #f5f6f8;
}
.cb-card { width: 430px; max-width: 100%; box-sizing: border-box; border-radius: 4px; padding: 8px 10px 4px; }
.head { text-align: center; margin-bottom: 24px; }
.head h2 { margin: 12px 0 4px; font-size: 19px; }
.logo {
  width: 54px; height: 54px; margin: 0 auto; border-radius: 14px;
  background: var(--ruc-red); color: #fff; font-size: 30px; font-family: serif;
  display: flex; align-items: center; justify-content: center;
}
.waiting { text-align: center; padding: 24px 0 32px; color: #6b7280; }
.waiting p { margin: 12px 0 0; }
</style>

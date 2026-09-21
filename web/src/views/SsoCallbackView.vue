<template>
  <div class="cb-wrap">
    <el-card class="cb-card">
      <div class="head">
        <div class="logo">志</div>
        <h2>志愿服务管理平台</h2>
      </div>

      <el-result v-if="error" icon="error" title="登录未完成" :sub-title="error">
        <template #extra>
          <el-button type="primary" @click="backToLogin">重新登录</el-button>
        </template>
      </el-result>

      <div v-else class="waiting">
        <el-icon class="is-loading" :size="26"><Loading /></el-icon>
        <p>正在完成统一身份认证…</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { api } from '../api'
import { setAuth } from '../store'

const router = useRouter()
const error = ref('')

onMounted(async () => {
  // CAS 把 ?code=..&state=.. 追加在平台根地址上，所以查询串在**真正的 URL** 里，
  // 不在 hash 里。必须从 window.location 取，读路由 query 会什么都拿不到。
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code') || ''
  const state = params.get('state') || ''

  // 立刻把授权码从地址栏抹掉。两个原因：它会进浏览器历史和 Referer；而且
  // 留着的话，之后每次路由跳转都会被守卫看见 search 里的 code、再弹回本页。
  window.history.replaceState(
    null, '', window.location.pathname + window.location.hash)

  if (!code || !state) {
    error.value = '回调地址里缺少 code 或 state，请从平台首页重新登录。'
    return
  }

  try {
    const d = await api.post('/api/auth/sso-callback', { code, state },
                             { skipErrorToast: true })
    setAuth(d.token, d.user)
    ElMessage.success(`欢迎，${d.user.name}`)
    // replace 而不是 push：带授权码的那一页不该留在前进/后退里。
    // next 是跳转前想去的页面（比如从签到二维码落地页被弹去登录的志愿者）。
    router.replace(d.next || '/')
  } catch (e) {
    error.value = e.message || '统一身份认证未通过，请重试。'
  }
})

function backToLogin() { router.replace('/login') }
</script>

<style scoped>
.cb-wrap {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background:
    radial-gradient(1000px 500px at 15% -10%, rgba(158,34,53,.18), transparent),
    radial-gradient(900px 500px at 110% 110%, rgba(31,90,168,.12), transparent),
    #f5f6f8;
}
.cb-card { width: 430px; border-radius: 16px; padding: 8px 10px 4px; }
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

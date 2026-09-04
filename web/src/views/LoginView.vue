<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <div class="head">
        <div class="logo">志</div>
        <h2>志愿服务管理平台</h2>
        <p class="dim">
          {{ mode === 'demo' ? '统一身份认证尚未接通，当前为开发组内测登录'
                             : '通过微人大统一身份认证登录' }}
        </p>
      </div>

      <!-- 真实 SSO：一个跳转按钮，账号口令都在学校那边 -->
      <template v-if="mode !== 'demo'">
        <el-button type="primary" size="large" style="width:100%"
                   :disabled="!loginUrl" @click="gotoSso">
          {{ loginUrl ? '前往统一身份认证登录' : '登录方式未配置，请联系管理员' }}
        </el-button>
      </template>

      <!-- 内测登录：学号 + 组内共享口令 -->
      <el-form v-else @submit.prevent>
        <el-input v-model="sid" size="large" placeholder="学号 / 工号"
                  clearable @keyup.enter="focusPassword">
          <template #prefix><el-icon><User /></el-icon></template>
        </el-input>
        <el-input ref="passwordInput" v-model="password" size="large" type="password"
                  placeholder="内测口令" show-password clearable
                  style="margin-top:10px" @keyup.enter="doLogin">
          <template #prefix><el-icon><Lock /></el-icon></template>
        </el-input>
        <el-button type="primary" size="large" style="width:100%;margin-top:14px"
                   :loading="loading" @click="doLogin">
          登录
        </el-button>
      </el-form>

      <template v-if="mode === 'demo' && accounts.length">
        <el-divider>体验账号（仍需口令）</el-divider>
        <div class="quick">
          <el-button v-for="a in accounts" :key="a.student_id" size="small"
                     plain @click="pick(a.student_id)">
            {{ a.label }}
          </el-button>
        </div>
      </template>

      <p class="foot dim">
        正式上线后由微人大 OAuth2.0/CAS 单点登录接管，内测口令入口将一并移除；<br />
        身份信息由「数智人大」只读接口提供，平台不留存多余数据。
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { setAuth } from '../store'

const router = useRouter()
const route = useRoute()
const sid = ref('')
const password = ref('')
const passwordInput = ref(null)
const loading = ref(false)
const accounts = ref([])
const mode = ref('')
const loginUrl = ref('')

onMounted(async () => {
  const m = await api.get('/api/auth/sso-mode', { silent: true })
  mode.value = m.mode
  loginUrl.value = m.login_url
  if (mode.value === 'demo') {
    accounts.value = await api.get('/api/auth/demo-accounts', { silent: true })
  }
})

function gotoSso() { location.href = loginUrl.value }

function pick(studentId) {
  sid.value = studentId
  focusPassword()
}
function focusPassword() { passwordInput.value?.focus() }

async function doLogin() {
  if (!sid.value.trim()) return ElMessage.warning('请输入学号/工号')
  if (!password.value) return ElMessage.warning('请输入内测口令')
  loading.value = true
  try {
    const d = await api.post('/api/auth/demo-login',
                             { student_id: sid.value.trim(), password: password.value })
    setAuth(d.token, d.user)
    ElMessage.success(`欢迎，${d.user.name}`)
    router.push(route.query.next || '/')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background:
    radial-gradient(1000px 500px at 15% -10%, rgba(158,34,53,.18), transparent),
    radial-gradient(900px 500px at 110% 110%, rgba(31,90,168,.12), transparent),
    #f5f6f8;
}
.login-card { width: 430px; border-radius: 16px; padding: 8px 10px 4px; }
.head { text-align: center; margin-bottom: 22px; }
.head h2 { margin: 10px 0 4px; font-size: 19px; }
.logo {
  width: 54px; height: 54px; margin: 0 auto; border-radius: 14px;
  background: var(--ruc-red); color: #fff; font-size: 30px; font-family: serif;
  display: flex; align-items: center; justify-content: center;
}
.quick { display: flex; flex-wrap: wrap; gap: 6px; }
.quick .el-button { margin-left: 0 !important; }
.foot { text-align: center; line-height: 1.8; margin-top: 16px; }
</style>

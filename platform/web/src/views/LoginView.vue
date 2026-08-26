<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <div class="head">
        <div class="logo">志</div>
        <h2>人大校青协志愿服务管理平台</h2>
        <p class="dim">通过微人大统一身份认证登录（演示环境为模拟认证）</p>
      </div>

      <el-form @submit.prevent>
        <el-input v-model="sid" size="large" placeholder="学号 / 工号"
                  clearable @keyup.enter="doLogin">
          <template #prefix><el-icon><User /></el-icon></template>
        </el-input>
        <el-button type="primary" size="large" style="width:100%;margin-top:14px"
                   :loading="loading" @click="doLogin">
          统一身份认证登录
        </el-button>
      </el-form>

      <el-divider>演示快捷入口</el-divider>
      <div class="quick">
        <el-button v-for="a in accounts" :key="a.student_id" size="small"
                   plain @click="sid = a.student_id; doLogin()">
          {{ a.label }}
        </el-button>
      </div>

      <p class="foot dim">
        生产环境将接入 OAuth2.0/CAS 单点登录；<br />
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
const loading = ref(false)
const accounts = ref([])

onMounted(async () => {
  try { accounts.value = await api.get('/api/auth/demo-accounts', { silent: true }) } catch {}
})

async function doLogin() {
  if (!sid.value.trim()) return ElMessage.warning('请输入学号/工号')
  loading.value = true
  try {
    const d = await api.post('/api/auth/login', { student_id: sid.value.trim() })
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

<template>
  <div class="scan-wrap">
    <LocaleSwitch float />
    <el-card class="scan-card" v-loading="loading">
      <template v-if="task">
        <div class="ok-icon" v-if="done">✓</div>
        <h2 style="margin:8px 0 4px">{{ task.activity_title }}</h2>
        <p class="dim" style="margin:0 0 12px">{{ t('志愿服装 · {0}码', [t(task.action_label)]) }}</p>

        <template v-if="!done">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item :label="t('当前状态')">
              <el-tag size="small" :type="stateType">{{ stateText }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item v-if="task.taken_at" :label="t('领取时间')">
              {{ fmtDT(task.taken_at) }}
            </el-descriptions-item>
          </el-descriptions>

          <el-button :type="task.action === 'take' ? 'primary' : 'warning'" size="large"
                     style="width:100%;margin-top:16px"
                     :disabled="!canAct" :loading="submitting" @click="doScan">
            {{ actionText }}
          </el-button>
        </template>
        <el-result v-else icon="success" :title="t('{0}成功', [t(task.action_label)])"
                   :sub-title="doneMsg">
          <template #extra>
            <el-button @click="$router.push('/my')">{{ t('查看我的报名') }}</el-button>
          </template>
        </el-result>

      </template>
      <el-result v-else-if="!loading" icon="warning" :title="t('此码无效')"
                 :sub-title="t('请确认扫的是本次活动的服装码')" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { auth } from '../store'
import { t } from '../i18n'
import LocaleSwitch from '../components/LocaleSwitch.vue'

const route = useRoute()
const router = useRouter()
const token = route.params.token
const task = ref(null)
const loading = ref(true)
const submitting = ref(false)
const done = ref(false)
const doneMsg = ref('')

const state = computed(() => {   // vuecheck: script-only
  const t = task.value
  if (!t) return 'none'
  if (!t.need_attire) return 'not_required'
  if (!t.active) return 'closed'
  if (t.action === 'take') return t.taken_at && !t.returned_at ? 'taken' : 'ready'
  if (!t.taken_at) return 'no_record'
  return t.returned_at ? 'returned' : 'ready'
})
const TEXT = {
  ready: '可以操作', taken: '您已领取，请按照要求归还',
  returned: '您已归还', closed: '该服装码已关闭',
  not_required: '您在本活动的服装不由平台统一发放',
  no_record: '没有您的领取记录',
}
const stateText = computed(() => t(TEXT[state.value] || ''))
const stateType = computed(() => (
  { ready: 'success', taken: 'warning', returned: 'info' }[state.value] || 'danger'))
const canAct = computed(() => state.value === 'ready')
const actionText = computed(() => t(
  task.value?.action === 'take' ? '领取服装' : '归还服装'))

async function load() {
  loading.value = true
  try {
    task.value = await api.get(`/api/attire/task/${token}`, { silent: true })
  } catch { task.value = null } finally { loading.value = false }
}
onMounted(() => {
  if (!auth.token) {
    // 扫码的人多半还没登录：先带去登录，登完回到这张服装码
    router.replace({ path: '/login', query: { next: `/attire/${token}` } })
    return
  }
  load()
})

async function doScan() {
  submitting.value = true
  try {
    const d = await api.post(`/api/attire/scan/${token}`)
    doneMsg.value = d.message
    done.value = true
    ElMessage.success(d.message)
  } finally { submitting.value = false }
}
</script>

<style scoped>
.scan-wrap { min-height: 100vh; display: flex; align-items: center;
             justify-content: center; padding: 20px; background: #f5f6f8; }
.scan-card { width: 100%; max-width: 420px; border-radius: 4px; text-align: center; }
.ok-icon { font-size: 44px; color: var(--el-color-success); line-height: 1; }
.foot { margin-top: 16px; line-height: 1.6; }
</style>

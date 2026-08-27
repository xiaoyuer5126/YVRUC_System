<template>
  <div class="scan-wrap">
    <el-card class="scan-card" v-loading="loading">
      <template v-if="task">
        <div class="ok-icon" v-if="done">✓</div>
        <h2 style="margin:6px 0 2px">{{ task.activity_title }}</h2>
        <p class="dim" style="margin:0 0 14px">{{ task.shift_name }} · {{ task.group_time }}</p>

        <template v-if="!done">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-if="task.position" label="您的岗位">
              {{ task.position.name }}（{{ task.position.location }}）
            </el-descriptions-item>
            <el-descriptions-item label="签到方式">
              {{ task.mode === 'position' ? '分岗位签到' : '统一签到' }}
            </el-descriptions-item>
            <el-descriptions-item label="当前状态">
              <template v-if="task.checked_in">
                已签到{{ task.checked_out ? ' · 已签退'
                        : (task.state === 'expired' ? ' · 未签退' : ' · 服务中') }}
              </template>
              <el-tag v-else size="small" :type="stateType">{{ stateText }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item v-if="task.my_checkin_at" label="签到时间">
              {{ task.my_checkin_at.replace('T', ' ') }}
            </el-descriptions-item>
          </el-descriptions>

          <el-button :type="task.checked_in ? 'warning' : 'primary'" size="large"
                     style="width:100%;margin-top:18px"
                     :disabled="task.state !== 'active' || !task.enrolled"
                     :loading="submitting" @click="doCheckin">
            <template v-if="!task.enrolled">您不是本场次确认志愿者</template>
            <template v-else-if="task.checked_in && !task.checked_out">签退离岗</template>
            <template v-else>签到到岗</template>
          </el-button>
          <p v-if="task.checked_in && !task.checked_out" class="dim" style="margin:10px 0 0">
            场次结束 2 小时内请完成签退；超时未签退将按场次结束时间计算时长。
          </p>
        </template>
        <template v-else>
          <el-result icon="success" :title="`${doneAction}成功`"
                     :sub-title="doneAt">
            <template #extra>
              <el-button @click="$router.push('/my')">查看我的报名</el-button>
            </template>
          </el-result>
        </template>

        <p class="dim foot">扫码数据实时回流青协后台签到大屏；<br />
          对接学校现有签到系统后由对方接口核销。</p>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'

const route = useRoute()
const task = ref(null)
const loading = ref(true)
const done = ref(false)
const doneAt = ref('')
const doneAction = ref('签到')
const submitting = ref(false)

const stateText = computed(() => ({
  active: '可签到', closed: '任务已关闭',
  early: '尚未开始', expired: '已截止',
}[task.value?.state] || ''))
const stateType = computed(() => task.value?.state === 'active' ? 'success' : 'info')

async function load() {
  loading.value = true
  try {
    task.value = await api.get(`/api/checkin/task/${route.params.token}`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!auth.token) {
    // 未登录先去登录（模拟扫码后需身份核验）
    localStorage.setItem('rucva_after_checkin', route.params.token)
    window.location.hash = '#/login?next=' + encodeURIComponent(`/checkin/${route.params.token}`)
    return
  }
  const pending = localStorage.getItem('rucva_after_checkin')
  if (pending && auth.token) localStorage.removeItem('rucva_after_checkin')
  await load()
})

async function doCheckin() {
  submitting.value = true
  try {
    const d = await api.post(`/api/checkin/task/${route.params.token}`)
    done.value = true
    doneAction.value = d.action === 'checkout' ? '签退' : '签到'
    doneAt.value = `${d.time.replace('T', ' ')} · ${d.message.split('，')[1] || ''}`
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.scan-wrap {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  padding: 16px;
  background:
    radial-gradient(800px 400px at 80% -10%, rgba(158,34,53,.15), transparent), #f5f6f8;
}
.scan-card { width: 420px; max-width: 100%; border-radius: 16px;
             text-align: center; padding: 8px; }

/* 这一页基本只在手机上被扫开：铺满、按钮做大、别垂直居中留一堆空白 */
@media (max-width: 860px) {
  .scan-wrap { align-items: flex-start; padding: 12px; }
  .scan-card { padding: 2px; }
  .scan-card :deep(.el-card__body) { padding: 16px 14px; }
  .scan-card :deep(.el-button--large) { height: 52px; font-size: 17px; }
  .scan-card :deep(.el-descriptions__label) { width: 78px; }
  h2 { font-size: 19px; line-height: 1.4; }
}
.ok-icon {
  width: 64px; height: 64px; margin: 4px auto; border-radius: 50%;
  background: #f0f9eb; color: #67c23a; font-size: 34px; line-height: 64px;
}
.foot { margin-top: 16px; line-height: 1.7; }
</style>

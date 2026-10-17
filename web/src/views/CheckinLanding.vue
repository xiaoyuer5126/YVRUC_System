<template>
  <div class="scan-wrap">
    <el-card class="scan-card" v-loading="loading">
      <template v-if="task">
        <div class="ok-icon" v-if="done">✓</div>
        <h2 style="margin:8px 0 4px">{{ task.activity_title }}</h2>
        <p class="dim" style="margin:0 0 12px">{{ task.shift_name }} · {{ task.group_time }}</p>

        <template v-if="!done">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-if="task.position" label="您的岗位">
              {{ task.position.name }}（{{ task.position.location }}）
            </el-descriptions-item>
            <el-descriptions-item label="签到方式">
              {{ task.method === 'geo' ? '定位签到'
                 : (task.mode === 'position' ? '分岗位签到' : '统一签到') }}
            </el-descriptions-item>
            <el-descriptions-item v-if="task.flexible_time" label="本场已签到">
              {{ task.checkin_count }} 次（时间灵活场次：每张签到码签一次到、一次退）
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

          <!-- 定位签到的场次现场没有码。多半是扫了张早先打印的旧码进来的，
               说清楚去哪儿签，别让人对着这个按钮点了报错 -->
          <el-alert v-if="task.method === 'geo'" type="info" :closable="false"
                    style="margin-top:16px;text-align:left">
            <p style="margin:0 0 8px;line-height:1.6">
              本场次用<b>定位签到</b>，不用扫码：「我的报名」里点「签到」，
              平台会比对您与签到点的距离。
            </p>
            <el-button size="small" type="primary" @click="$router.push('/my')">
              去我的报名签到
            </el-button>
          </el-alert>
          <el-button v-else :type="task.checked_in ? 'warning' : 'primary'" size="large"
                     style="width:100%;margin-top:16px"
                     :disabled="task.state !== 'active' || !task.enrolled
                                || (task.flexible_time && task.checked_out)"
                     :loading="submitting" @click="doCheckin">
            <template v-if="!task.enrolled">您不是本场次志愿者，请联系负责人</template>
            <template v-else-if="task.checked_in && !task.checked_out">签退离岗</template>
            <template v-else-if="task.flexible_time && task.checked_out">这张码已签到签退，下次请扫新码</template>
            <template v-else>签到到岗</template>
          </el-button>
          <!-- 签退截止就是这张码的截止（后端 checkout_deadline），不写死几小时：
               可签时段是管理者定的。过了它还没签退，时长按场次结束时间（时间灵活的
               场次按这张码的截止时间）记、待核 -->
          <p v-if="task.checked_in && !task.checked_out" class="dim" style="margin:12px 0 0">
            <template v-if="task.state === 'active' || task.state === 'early'">
              离岗时请签退，签退截止 {{ task.checkout_deadline.replace('T', ' ') }}；
              逾期未签退的，时长按{{ task.flexible_time ? '签到码截止时间' : '场次结束时间' }}记并由负责人核对
            </template>
            <template v-else>
              签到码已{{ task.state === 'closed' ? '关闭' : '截止' }}，无法自行签退；
              时长按{{ task.flexible_time ? '签到码截止时间' : '场次结束时间' }}记并由负责人核对，有出入请联系负责人
            </template>
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

      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'

const route = useRoute()
const router = useRouter()
const task = ref(null)
const loading = ref(true)
const done = ref(false)
const doneAt = ref('')
const doneAction = ref('签到')
const submitting = ref(false)
// 扫的是动态码、又绕去登录了一趟：回来时那张码多半已经换掉了
const afterLogin = ref(false)   // vuecheck: script-only，只用来挑报错的说法

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
    // 动态码只活一分钟，登录这几步走完就过期了。只说「签到码无效」会让人
    // 对着屏幕反复刷新——直接告诉他回现场再扫一次。
    ElMessage.error(afterLogin.value
      ? '这个签到码已经失效了（现场码每分钟更换），请重新扫一次现场的二维码'
      : e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!auth.token) {
    // 未登录先去登录（模拟扫码后需身份核验）
    localStorage.setItem('yvruc_after_checkin', route.params.token)
    router.replace({ path: '/login', query: { next: `/checkin/${route.params.token}` } })
    return
  }
  const pending = localStorage.getItem('yvruc_after_checkin')
  if (pending && auth.token) {
    localStorage.removeItem('yvruc_after_checkin')
    afterLogin.value = true
  }
  await load()
})

async function doCheckin() {
  submitting.value = true
  try {
    const d = await api.post(`/api/checkin/task/${route.params.token}`)
    done.value = true
    doneAction.value = d.action === 'checkout' ? '签退' : '签到'
    // 不从 message 里截后半句：那句话是写给「别人」的（「张三 已到岗。」），
    // 放在自己的成功页上既啰嗦又像在念别人的名字。按动作自己说一句。
    doneAt.value = `${d.time.replace('T', ' ')} · ${d.action === 'checkout'
      ? '本次服务时长将按签到签退计算' : '已到岗'}`
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
    radial-gradient(800px 400px at 80% -10%, rgba(174,11,42,.15), transparent), #f5f6f8;
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
.foot { margin-top: 16px; line-height: 1.6; }
</style>

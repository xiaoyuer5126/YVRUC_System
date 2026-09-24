<template>
  <div v-loading="loading">
    <div class="toolbar">
      <!-- 码是幂等的，生成过就不必再点；按钮留着只会让人以为还能再生成一套 -->
      <el-button v-if="!data?.tasks?.take" type="primary" size="small" @click="ensureTasks">
        <el-icon><Plus /></el-icon>&nbsp;生成领取 / 归还码
      </el-button>
      <el-button v-if="data?.tasks?.take" size="small" @click="openQR('take')">领取码</el-button>
      <el-button v-if="data?.tasks?.return" size="small" @click="openQR('return')">归还码</el-button>
      <el-button size="small" type="primary" plain :disabled="!data?.not_taken"
                 @click="openRemind('take')">
        提醒领取（{{ data?.not_taken || 0 }} 人）
      </el-button>
      <el-button size="small" type="warning" plain :disabled="!data?.outstanding"
                 @click="openRemind('return')">
        提醒归还（{{ data?.outstanding || 0 }} 人）
      </el-button>
      <RefreshBar ref="refresher" :load="load" />
      <span class="dim">领取与归还各一个码，现场也可手动登记</span>
    </div>

    <el-row :gutter="16" style="margin-bottom:12px">
      <el-col :span="6"><el-card shadow="never" class="num"><b>{{ data?.expected || 0 }}</b><div class="dim">应领</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never" class="num"><b>{{ (data?.taken || 0) + (data?.returned || 0) }}</b><div class="dim">已领</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never" class="num"><b>{{ data?.returned || 0 }}</b><div class="dim">已归还</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never" class="num warn"><b>{{ data?.outstanding || 0 }}</b><div class="dim">未归还</div></el-card></el-col>
    </el-row>

    <!-- 勾一批再点，一次登记一整个岗位；单个的人就在他那一行上点（见手动登记列） -->
    <div class="toolbar">
      <template v-if="selected.length">
        <span class="dim">已选 {{ selected.length }} 人：</span>
        <el-button size="small" type="primary" @click="batch('take')">登记领取</el-button>
        <el-button size="small" type="warning" @click="batch('return')">登记归还</el-button>
        <el-button size="small" plain @click="batch('cancel_return')">撤销归还</el-button>
        <el-button size="small" type="danger" plain @click="batch('cancel_take')">撤销领取</el-button>
      </template>
      <span v-else class="dim">勾选多个人可批量登记；单个人直接在他那一行点</span>
    </div>

    <DataCards v-if="narrow" :rows="data?.items || []" title-key="name"
               row-key="student_id" :selectable="true" @selection-change="onSelect"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'position', label: '岗位' }]"
               empty="还没有需要领取服装的志愿者">
      <template #extra="{ row }">
        <el-tag size="small" :type="tagType(row)">{{ tagText(row) }}</el-tag>
      </template>
      <template #actions="{ row }">
        <AttireOps :row="row" @do="one" />
      </template>
    </DataCards>
    <el-table v-else :data="data?.items || []" size="small" max-height="360"
              @selection-change="onSelect">
      <el-table-column type="selection" width="40" />
      <el-table-column prop="name" label="姓名" width="110" />
      <el-table-column prop="student_id" label="学号" width="120" />
      <el-table-column prop="position" label="岗位" min-width="130" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="tagType(row)">{{ tagText(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="领取 / 归还" min-width="170">
        <template #default="{ row }">
          <span class="dim">{{ fmtDT(row.taken_at) || '—' }}</span>
          <span class="dim"> / {{ fmtDT(row.returned_at) || '—' }}</span>
        </template>
      </el-table-column>
      <!-- 现场没网、码贴反了都得有人工兜底，所以按钮就放在人这一行上 -->
      <el-table-column label="手动登记" width="210" fixed="right">
        <template #default="{ row }"><AttireOps :row="row" @do="one" /></template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="rm.visible" :title="rm.action === 'take' ? '提醒领取服装' : '提醒归还服装'"
               width="440">
      <p class="dim" style="line-height:1.8">
        {{ rm.action === 'take'
           ? '发给该范围内尚未领取服装的志愿者。'
           : '发给该范围内尚未归还服装的志愿者。' }}
      </p>
      <!-- 现场往往是「东门那几个人还没还」或者「通州校区那批」，
           整场群发等于给已经还了的人白推一条。可以不选、可以选几个 -->
      <el-form label-width="72px">
        <el-form-item label="场次">
          <el-select v-model="rm.shiftIds" multiple collapse-tags
                     collapse-tags-tooltip clearable placeholder="全部场次"
                     style="width:100%">
            <el-option v-for="g in allShifts" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.time_text}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="校区">
          <el-select v-model="rm.campuses" multiple collapse-tags
                     collapse-tags-tooltip clearable placeholder="全部校区"
                     style="width:100%">
            <el-option v-for="c in campuses" :key="c" :value="c" :label="c" />
          </el-select>
        </el-form-item>
      </el-form>
      <el-checkbox v-model="rm.needAck">要求志愿者点「我已知悉」</el-checkbox>
      <div class="dim">勾选后可在「签收情况」查看并一键提醒。</div>
      <template #footer>
        <el-button @click="rm.visible = false">取消</el-button>
        <el-button type="primary" :loading="rm.busy" @click="remind">发送</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qr.visible" :title="'服装' + qr.label + '码'" width="380">
      <div style="text-align:center" v-if="qr.token">
        <!-- 必须带 base：不传的话码里编的是相对路径，相机只会当一行字显示 -->
        <img :src="`/api/attire/qr.svg?token=${qr.token}&base=${qrBase()}&t=${Date.now()}`"
             width="260" height="260" alt="服装二维码" />
        <!-- 不出那串码、也不出链接：能念给群里、能转发的凭据，等于谁都能替别人领。
             要看码只有一条路——把这一屏亮在来领的人面前（同签到二维码那边） -->
        <div>{{ title }}·服装{{ qr.label }}码</div>
        <div class="dim">请把这一屏亮在{{ qr.label }}处，别和另一张弄混</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { api, fmtDT, qrBase } from '../api'
import { useNarrow } from '../composables/useNarrow'
import AttireOps from './AttireOps.vue'
import DataCards from './DataCards.vue'
import RefreshBar from './RefreshBar.vue'

const props = defineProps({
  activityId: { type: String, required: true },
  // 二维码弹窗上要写「活动名·服装领取码」：那一屏是贴在现场的，
  // 光看码分不出是哪场活动的哪个码
  title: { type: String, default: '' },
  // 提醒时能挑场次
  allShifts: { type: Array, default: () => [] },
})
const narrow = useNarrow()
const data = ref(null)
const loading = ref(false)
const selected = ref([])      // 批量登记勾的人
const qr = ref({ visible: false, token: '', label: '' })
const refresher = ref(null)
const campuses = ref([])      // 校区清单以后端为准，别在这儿抄第二份

async function load() {
  loading.value = true
  try {
    data.value = await api.get(`/api/staff/activities/${props.activityId}/attire`)
    selected.value = []       // 名单重拉之后，之前勾的那批已经不是同一批人了
  } finally { loading.value = false }
}
const onSelect = rows => { selected.value = rows }
onMounted(async () => {
  await load()
  refresher.value?.markLoaded()
  try {
    campuses.value = (await api.get('/api/auth/constants', { silent: true })).campuses || []
  } catch { /* 拿不到就只按场次筛 */ }
})

const tagText = r => (r.returned ? '已归还' : r.taken ? '未归还' : '未领取')
const tagType = r => (r.returned ? 'success' : r.taken ? 'warning' : 'info')

async function ensureTasks() {
  await api.post(`/api/staff/activities/${props.activityId}/attire-tasks`, {})
  ElMessage.success('领取码与归还码已就绪')
  load()
}

function openQR(action) {
  qr.value = { visible: true, token: data.value.tasks[action].token,
               label: action === 'take' ? '领取' : '归还' }
}

/** 勾一批一起登记：现场常常是「这个岗位的十几个人一次领走」。 */
async function batch(action) {
  await api.post(`/api/staff/activities/${props.activityId}/attire/batch`,
    { action, student_ids: selected.value.map(r => r.student_id) })
  ElMessage.success(`已登记 ${selected.value.length} 人`)
  load()
}

async function one(action, row) {
  await api.post(`/api/staff/activities/${props.activityId}/attire/batch`,
    { action, student_ids: [row.student_id] })
  ElMessage.success('已登记')
  load()
}

const rm = ref({ visible: false, action: 'return', shiftIds: [], campuses: [],
                 needAck: false, busy: false })
const openRemind = action => {
  rm.value = { visible: true, action, shiftIds: [], campuses: [],
               needAck: false, busy: false }
}

async function remind() {
  rm.value.busy = true
  try {
    const d = await api.post(`/api/staff/activities/${props.activityId}/attire/remind`,
      { action: rm.value.action, shift_ids: rm.value.shiftIds,
        campuses: rm.value.campuses, need_ack: rm.value.needAck })
    ElMessage.success(`已提醒 ${d.count} 位志愿者（${d.scope}）`
      + (d.batch_id ? '，要求签收' : ''))
    rm.value.visible = false
  } finally { rm.value.busy = false }
}
</script>

<style scoped>
.num { text-align: center; border-radius: 12px; }
.num b { font-size: 26px; color: var(--ruc-red); }
.num.warn b { color: var(--el-color-warning); }
</style>

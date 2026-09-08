<template>
  <div class="page">
    <h2>我的报名</h2>

    <!-- 窄屏：一条报名一张卡；表格在手机上要么横滑要么挤成一条线 -->
    <div v-if="narrow" v-loading="loading" class="mob-list">
      <div v-for="row in rows" :key="row.id" class="mob-card">
        <div class="mob-head">
          <b>{{ row.activity.title }}</b>
          <EnrollStatusTag :row="row" />
        </div>
        <div class="dim">{{ row.activity.location }}</div>
        <div class="mob-line">
          <el-tag v-if="row.shift.attached" size="small" effect="dark"
                  :type="row.shift.kind === 'training' ? 'success' : 'warning'">
            {{ row.shift.kind_label }}
          </el-tag>
          {{ row.shift.name }} · {{ row.shift.time_text }}
        </div>
        <div v-if="row.shift.attached" class="dim">
          随「{{ row.shift.parent_name }}」报名，退选请退主场次
          <template v-if="row.shift.location"> · {{ row.shift.location }}</template>
          <el-tag v-if="row.excused" size="small" type="info" effect="plain">已请假</el-tag>
        </div>
        <div v-if="row.position" class="mob-line">
          <el-tag size="small" effect="plain">{{ row.position.name }}</el-tag>
          <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
          <span class="dim"> {{ row.position.location }}</span>
        </div>
        <div v-else-if="row.status === 'enrolled'" class="dim">岗位待分配</div>
        <div class="mob-line">
          <span class="dim">岗位调剂：</span>
          <template v-if="isActive(row) && !row.shift.attached">
            <el-tag size="small" effect="plain"
                    :type="row.allow_reassign ? 'success' : 'info'">
              {{ row.allow_reassign ? '接受' : '不接受' }}
            </el-tag>
            <el-button size="small" link type="primary"
                       @click="toggleReassign(row)">修改</el-button>
          </template>
          <span v-else class="dim">—</span>
          <template v-if="row.status === 'enrolled'">
            <span class="dim" style="margin-left:10px">签到：</span>
            <CheckinStatusTag :row="row" />
          </template>
        </div>
        <div class="mob-actions">
          <el-button size="small" @click="$router.push('/activity/' + row.activity.id)">
            查看活动
          </el-button>
          <el-button v-if="row.contact_group_qr" size="small" plain
                     @click="showQr(row)">查看联络群</el-button>
          <el-button v-if="row.is_leader" size="small" type="warning" plain
                     @click="openTeam(row)">组员名单</el-button>
          <el-button v-if="row.status === 'pending_confirm'" size="small" type="primary"
                     @click="confirm(row)">确认转正</el-button>
          <el-button v-else-if="row.status === 'applied'" size="small" type="danger" plain
                     @click="withdraw(row)">撤回申请</el-button>
          <el-button v-else-if="row.active !== false && isActive(row) && !row.shift.attached"
                     size="small" type="danger" plain @click="cancel(row)">
            {{ row.status === 'waitlisted' ? '退出候补' : '退选' }}
          </el-button>
        </div>
      </div>
      <el-empty v-if="!rows.length && !loading" description="还没有报名记录" :image-size="60" />
    </div>

    <el-table v-else :data="rows" style="width:100%" v-loading="loading">
      <el-table-column label="活动" min-width="200">
        <template #default="{ row }">
          <b>{{ row.activity.title }}</b>
          <div class="dim">{{ row.activity.location }}</div>
        </template>
      </el-table-column>
      <el-table-column label="场次 / 岗位" min-width="180">
        <template #default="{ row }">
          <el-tag v-if="row.shift.attached" size="small" effect="dark"
                  :type="row.shift.kind === 'training' ? 'success' : 'warning'">
            {{ row.shift.kind_label }}
          </el-tag>
          {{ row.shift.name }}（{{ row.shift.time_text }}）
          <div v-if="row.shift.attached" class="dim">
            随「{{ row.shift.parent_name }}」报名
            <template v-if="row.shift.location"> · {{ row.shift.location }}</template>
            <el-tag v-if="row.excused" size="small" type="info" effect="plain">已请假</el-tag>
          </div>
          <div v-if="row.position">
            <el-tag size="small" effect="plain">{{ row.position.name }}</el-tag>
            <span class="dim"> {{ row.position.time_text }} · {{ row.position.location }}</span>
          </div>
          <div v-else-if="row.status === 'enrolled'" class="dim">岗位待分配</div>
        </template>
      </el-table-column>
      <el-table-column label="岗位调剂" width="160">
        <template #default="{ row }">
          <!-- 状态归状态、操作归操作：点标签切换太隐蔽，也容易误触 -->
          <template v-if="isActive(row) && !row.shift.attached">
            <el-tag size="small" effect="plain"
                    :type="row.allow_reassign ? 'success' : 'info'">
              {{ row.allow_reassign ? '接受' : '不接受' }}
            </el-tag>
            <el-tooltip placement="top"
                        content="仅在名额不足时可能进行调剂，负责人可看到你的意愿">
              <el-button size="small" link type="primary"
                         @click="toggleReassign(row)">修改</el-button>
            </el-tooltip>
          </template>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="190">
        <template #default="{ row }"><EnrollStatusTag :row="row" /></template>
      </el-table-column>
      <el-table-column label="签到" width="120">
        <template #default="{ row }">
          <CheckinStatusTag v-if="row.status === 'enrolled'" :row="row" />
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="$router.push('/activity/' + row.activity.id)">查看</el-button>
          <el-button v-if="row.contact_group_qr" size="small" plain
                     @click="showQr(row)">联络群</el-button>
          <el-button v-if="row.is_leader" size="small" type="warning" plain
                     @click="openTeam(row)">组员</el-button>
          <el-button v-if="row.status === 'pending_confirm'" size="small" type="primary"
                     @click="confirm(row)">确认</el-button>
          <el-button v-else-if="row.status === 'applied'" size="small" type="danger" plain
                     @click="withdraw(row)">撤回申请</el-button>
          <el-button v-else-if="row.active !== false && isActive(row) && !row.shift.attached"
                     size="small" type="danger" plain @click="cancel(row)">
            {{ row.status === 'waitlisted' ? '退出候补' : '退选' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-alert type="info" :closable="false" style="margin-top:16px">
      <p style="margin:0;font-size:12.5px;line-height:1.9">
        退选规则：开始前 <b>24 小时内</b>且报名已截止时退选，名额将被扣留不再释放，且记录一次临期退选。
      </p>
    </el-alert>

    <GroupQrDialog ref="groupQr" />

    <el-dialog v-model="teamDlg.visible" :title="'组员名单 · ' + (teamDlg.data?.scope || '')"
               width="560">
      <p class="dim" style="margin-top:0;line-height:1.8">
        {{ teamDlg.data?.activity_title }} · {{ teamDlg.data?.shift_name }}。
        联系方式仅用于本次活动的联络，请勿他用；<b>每次查看都会记入审计</b>。
      </p>
      <DataCards v-if="narrow" :rows="teamDlg.data?.members || []" title-key="name"
                 :fields="[{ key: 'position', label: '岗位' },
                           { key: 'college', label: '学院' },
                           { key: 'phone', label: '手机' },
                           { key: 'wechat', label: '微信' }]"
                 empty="本组暂无组员">
        <template #extra="{ row }">
          <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
        </template>
      </DataCards>
      <el-table v-else :data="teamDlg.data?.members || []" size="small">
        <el-table-column label="姓名" width="110">
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="position" label="岗位" min-width="110" />
        <el-table-column prop="college" label="学院" min-width="120" />
        <el-table-column prop="phone" label="手机" width="130" />
        <el-table-column prop="wechat" label="微信" min-width="110" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useNarrow } from '../composables/useNarrow'
import EnrollStatusTag from '../components/EnrollStatusTag.vue'
import CheckinStatusTag from '../components/CheckinStatusTag.vue'
import GroupQrDialog from '../components/GroupQrDialog.vue'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const rows = ref([])
const loading = ref(false)
const teamDlg = ref({ visible: false, data: null })
const groupQr = ref(null)

function showQr(row) {
  groupQr.value.open({ qr: row.contact_group_qr, title: row.activity.title })
}

// 组长看本组组员的联系方式。范围是同一岗位组，后端每次查看都留痕。
async function openTeam(row) {
  teamDlg.value = { visible: true, data: null }
  teamDlg.value.data = await api.get(`/api/me/enrollments/${row.id}/team`)
}

function isActive(r) {
  return ['enrolled', 'waitlisted', 'pending_confirm'].includes(r.status)
}


async function load() {
  loading.value = true
  try { rows.value = await api.get('/api/me/enrollments') } finally { loading.value = false }
}
onMounted(load)

async function withdraw(row) {
  await ElMessageBox.confirm('撤回后可以重新申请。确定撤回吗？', '撤回申请', { type: 'warning' })
  await api.del(`/api/activities/${row.activity.id}/apply`)
  ElMessage.success('已撤回申请')
  load()
}

async function confirm(row) {
  const d = await api.post(`/api/activities/enrollments/${row.id}/confirm-promotion`)
  ElMessage.success(d.message)
  load()
}

async function toggleReassign(row) {
  const d = await api.patch(`/api/activities/enrollments/${row.id}/reassign-pref`,
                            { allow_reassign: !row.allow_reassign })
  row.allow_reassign = d.allow_reassign
  ElMessage.success(d.allow_reassign ? '已设为接受岗位调剂' : '已设为不接受岗位调剂')
}

async function cancel(row) {
  const g = row.shift
  const start = new Date(g.start_time.replace(' ', 'T'))
  const late = Date.now() > start.getTime() - 24 * 3600e3
  await ElMessageBox.confirm(
    late ? '距开始不足24小时，将记录一次临期退选。确定退选吗？' : '确定退选吗？',
    '退选确认', { type: 'warning', confirmButtonText: '确定' })
  const d = await api.del(`/api/activities/enrollments/${row.id}`)
  ElMessage[d.effect === 'held' ? 'warning' : 'success'](d.message)
  load()
}
</script>

<style scoped>
.mob-list { display: flex; flex-direction: column; gap: 10px; }
.mob-card { background:#fff; border:1px solid #eceef1; border-radius:10px; padding:12px 14px; }
.mob-head { display:flex; justify-content:space-between; gap:8px; align-items:flex-start;
            margin-bottom:2px; }
.mob-head b { font-size:14.5px; line-height:1.45; }
.mob-line { margin-top:6px; font-size:13px; display:flex; align-items:center;
            gap:4px; flex-wrap:wrap; }
.mob-actions { margin-top:10px; padding-top:10px; border-top:1px dashed #eceef1;
               display:flex; gap:8px; flex-wrap:wrap; }
.checkout-time {
  font-size: 11px;
  line-height: 1.4;
  margin-top: 2px;
}
</style>

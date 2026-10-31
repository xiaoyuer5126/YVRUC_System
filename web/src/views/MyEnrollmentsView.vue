<template>
  <div class="page">
    <h2>{{ t('我的报名') }}</h2>

    <!-- 窄屏：一条报名一张卡；表格在手机上要么横滑要么挤成一条线 -->
    <div v-if="narrow" v-loading="loading" class="mob-list">
      <div v-for="row in rows" :key="row.id" class="mob-card">
        <div class="mob-head">
          <b class="title" @click="$router.push('/activity/' + row.activity.id)">
            {{ row.activity.title }}
          </b>
          <EnrollStatusTag :row="row" />
          <el-tag v-if="row.status === 'enrolled' && row.list_locked" size="small" type="info">{{ t('名单已锁定') }}</el-tag>
        </div>
        <div class="dim">{{ row.activity.location }}</div>
        <div class="mob-line">
          <el-tag v-if="row.shift.attached" size="small" effect="dark"
                  :type="row.shift.kind === 'training' ? 'success' : 'warning'">
            {{ t(row.shift.kind_label) }}
          </el-tag>
          {{ row.shift.name }} · {{ row.shift.time_text }}
        </div>
        <div v-if="(row.shift.periods || []).length > 1 && row.status === 'enrolled'"
             class="dim">
          <el-tag v-for="p in row.shift.periods" :key="p.id" size="small" effect="plain"
                  :type="p.checked_out ? 'success' : p.checked_in ? 'warning' : 'info'"
                  style="margin:0 4px 4px 0">
            {{ p.text }} · {{ t(p.checked_out ? '已签退' : p.checked_in ? '已签到' : '未签到') }}
          </el-tag>
        </div>
        <div v-if="row.shift.attached" class="dim">{{ t('随服务场次的报名产生；不能参加请到活动页请假') }}<template v-if="row.shift.location"> · {{ row.shift.location }}</template>
          <el-tag v-if="row.excused" size="small" type="info" effect="plain">{{ t('已请假') }}</el-tag>
        </div>
        <div v-if="row.position" class="mob-line">
          <el-tag size="small" effect="plain">{{ row.position.name }}</el-tag>
          <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">{{ t('组长') }}</el-tag>
          <span class="dim"> {{ row.position.location }}</span>
        </div>
        <div v-else-if="row.status === 'enrolled'" class="dim">{{ t('岗位待分配') }}</div>
        <div v-if="row.is_leader && row.leader_task" class="dim">{{ t('组长任务：{0}', [row.leader_task]) }}</div>
        <div class="mob-line">
          <span class="dim">{{ t('岗位调剂：') }}</span>
          <template v-if="isActive(row) && !row.shift.attached">
            <el-tag size="small" effect="plain"
                    :type="row.allow_reassign ? 'success' : 'info'">
              {{ t(row.allow_reassign ? '接受调剂' : '不接受调剂') }}
            </el-tag>
            <el-button size="small" link type="primary"
                       @click="toggleReassign(row)">{{ t('修改') }}</el-button>
          </template>
          <span v-else class="dim">—</span>
          <template v-if="row.status === 'enrolled'">
            <span class="dim" style="margin-left:12px">{{ t('签到：') }}</span>
            <CheckinStatusTag :row="row" />
          </template>
        </div>
        <div class="mob-actions">
          <!-- 手机上签到是这一页最主要的动作，所以排在第一个 -->
          <el-button v-if="signAction(row)" size="small"
                     :type="isCheckout(row) ? 'warning' : 'primary'"
                     @click="openCheckin(row)">{{ signAction(row) }}</el-button>
          <el-button v-if="row.contact_group_qr" size="small" plain
                     @click="showQr(row)">{{ t('查看联络群') }}</el-button>
          <el-button v-if="row.is_leader" size="small" type="warning" plain
                     @click="openTeam(row)">{{ t('组员名单') }}</el-button>
          <el-button v-if="row.status === 'pending_confirm'" size="small" type="primary"
                     @click="confirm(row)">{{ t('确认转正') }}</el-button>
          <el-button v-else-if="row.status === 'applied'" size="small" type="danger" plain
                     @click="withdraw(row)">{{ t('撤回申请') }}</el-button>
          <el-button v-else-if="row.active !== false && isActive(row) && !row.shift.attached && !started(row)"
                     size="small" type="danger" plain @click="cancel(row)">
            {{ t(row.status === 'waitlisted' ? '退出候补' : '退选') }}
          </el-button>
        </div>
      </div>
      <el-empty v-if="!rows.length && !loading" :description="t('还没有报名记录')" />
    </div>

    <el-table v-else :data="rows" style="width:100%" v-loading="loading">
      <el-table-column :label="t('活动')" min-width="260">
        <template #default="{ row }">
          <b class="title" @click="$router.push('/activity/' + row.activity.id)">
            {{ row.activity.title }}
          </b>
          <div class="dim">{{ row.activity.location }}</div>
        </template>
      </el-table-column>
      <el-table-column :label="t('场次 / 岗位')" min-width="240">
        <template #default="{ row }">
          <el-tag v-if="row.shift.attached" size="small" effect="dark"
                  :type="row.shift.kind === 'training' ? 'success' : 'warning'">
            {{ t(row.shift.kind_label) }}
          </el-tag>
          {{ row.shift.name }}（{{ row.shift.time_text }}）
          <div v-if="row.shift.attached" class="dim">{{ t('随服务场次的报名产生') }}<template v-if="row.shift.location"> · {{ row.shift.location }}</template>
            <el-tag v-if="row.excused" size="small" type="info" effect="plain">{{ t('已请假') }}</el-tag>
          </div>
          <!-- 分段签到的场次：逐段列出签到状态，宽屏窄屏一样 -->
          <div v-if="(row.shift.periods || []).length > 1 && row.status === 'enrolled'"
               class="dim">
            <el-tag v-for="p in row.shift.periods" :key="p.id" size="small" effect="plain"
                    :type="p.checked_out ? 'success' : p.checked_in ? 'warning' : 'info'"
                    style="margin:4px 4px 0 0">
              {{ p.text }} · {{ t(p.checked_out ? '已签退' : p.checked_in ? '已签到' : '未签到') }}
            </el-tag>
          </div>
          <div v-if="row.position">
            <el-tag size="small" effect="plain">{{ row.position.name }}</el-tag>
            <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">{{ t('组长') }}</el-tag>
            <!-- 时段跟场次那行重复：这里只留岗位地点（手机端也是这么显示的） -->
            <span v-if="row.position.location" class="dim"> {{ row.position.location }}</span>
          </div>
          <div v-else-if="row.status === 'enrolled'" class="dim">{{ t('岗位待分配') }}</div>
          <div v-if="row.is_leader && row.leader_task" class="dim">{{ t('组长任务：{0}', [row.leader_task]) }}</div>
        </template>
      </el-table-column>
      <el-table-column :label="t('岗位调剂')" width="120">
        <template #default="{ row }">
          <!-- 状态归状态、操作归操作：点标签切换太隐蔽，也容易误触 -->
          <template v-if="isActive(row) && !row.shift.attached">
            <el-tag size="small" effect="plain"
                    :type="row.allow_reassign ? 'success' : 'info'">
              {{ t(row.allow_reassign ? '接受调剂' : '不接受调剂') }}
            </el-tag>
            <el-tooltip placement="top"
                        :content="t('仅在名额不足时可能进行调剂，负责人可看到你的意愿')">
              <el-button size="small" link type="primary"
                         @click="toggleReassign(row)">{{ t('修改') }}</el-button>
            </el-tooltip>
          </template>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('状态')" width="150">
        <template #default="{ row }">
          <EnrollStatusTag :row="row" />
          <el-tag v-if="row.status === 'enrolled' && row.list_locked" size="small" type="info">{{ t('名单已锁定') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('签到')" width="120">
        <template #default="{ row }">
          <CheckinStatusTag v-if="row.status === 'enrolled'" :row="row" />
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('操作')" width="225" fixed="right">
        <template #default="{ row }">
          <div class="btn-row">
            <el-button v-if="signAction(row)" size="small"
                       :type="isCheckout(row) ? 'warning' : 'primary'"
                       @click="openCheckin(row)">{{ signAction(row) }}</el-button>
            <el-button v-if="row.contact_group_qr" size="small" plain
                       @click="showQr(row)">{{ t('查看联络群') }}</el-button>
            <el-button v-if="row.is_leader" size="small" type="warning" plain
                       @click="openTeam(row)">{{ t('组员名单') }}</el-button>
            <el-button v-if="row.status === 'pending_confirm'" size="small" type="primary"
                       @click="confirm(row)">{{ t('确认转正') }}</el-button>
            <el-button v-else-if="row.status === 'applied'" size="small" type="danger" plain
                       @click="withdraw(row)">{{ t('撤回申请') }}</el-button>
            <el-button v-else-if="row.active !== false && isActive(row) && !row.shift.attached && !started(row)"
                       size="small" type="danger" plain @click="cancel(row)">
              {{ t(row.status === 'waitlisted' ? '退出候补' : '退选') }}
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-alert type="info" :closable="false">
      <p style="margin:0;font-size:12.5px;line-height:1.6">{{ t('退选规则：每个场次开场前一段时间会锁定名单（活动页上写着几小时），负责人也可能提前锁定； 名单锁定后退出会被记录一次。') }}</p>
    </el-alert>

    <GroupQrDialog ref="groupQr" />

    <ScanCheckinDialog v-model="scanVisible" :action="t(scanLabel)" @done="onChecked" />

    <el-dialog v-model="teamDlg.visible" :title="t('组员名单 · {0}', [teamDlg.data?.scope || ''])"
               width="560">
      <p class="dim">
        {{ t('{0} · {1}。联系方式仅用于本次活动的联络，请勿他用；', [teamDlg.data?.activity_title, teamDlg.data?.shift_name]) }}<b>{{ t('每次查看都会记入审计') }}</b>。
      </p>
      <DataCards v-if="narrow" :rows="teamDlg.data?.members || []" title-key="name"
                 :fields="[{ key: 'position', label: t('岗位') },
                           { key: 'college', label: t('学院') },
                           { key: 'phone', label: t('手机') },
                           { key: 'wechat', label: t('微信') }]"
                 :empty="t('本组暂无组员')">
        <template #extra="{ row }">
          <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">{{ t('组长') }}</el-tag>
        </template>
      </DataCards>
      <el-table v-else :data="teamDlg.data?.members || []" size="small">
        <el-table-column :label="t('姓名')" width="110">
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">{{ t('组长') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="position" :label="t('岗位')" min-width="110" />
        <el-table-column prop="college" :label="t('学院')" min-width="120" />
        <el-table-column prop="phone" :label="t('手机')" width="130" />
        <el-table-column prop="wechat" :label="t('微信')" min-width="110" />
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
import ScanCheckinDialog from '../components/ScanCheckinDialog.vue'
import DataCards from '../components/DataCards.vue'
import { t } from '../i18n'

const narrow = useNarrow()
const rows = ref([])
const loading = ref(false)
const teamDlg = ref({ visible: false, data: null })
const groupQr = ref(null)
const scanVisible = ref(false)
const scanLabel = ref('签到')   // 这轮扫的是签到还是签退，弹窗照着它说话；存中文原句，模板里再 t()
// 取坐标要几秒，连点两下就是两次签到请求。界面上的「正在定位…」由 geoCheckin 自己弹
const geoBusy = ref(false)   // vuecheck: script-only

/** 这一行现在能不能签 / 该显示什么按钮。
 *
 *  码不在这里：接口只回状态（见后端 me._checkin_state），真正的凭据是现场
 *  扫出来的。所以扫码模式下按钮只负责调起扫一扫，点它本身签不了到——否则
 *  躺在床上也能点，动态码那道防线就白设了。 */
function signAction(row) {
  const c = row.checkin
  if (!c?.open || row.status !== 'enrolled') return ''
  if (!row.checked_in) return t('签到')
  return row.checkout_at ? '' : t('签退')
}

// 这一下点的是签退：按钮换成警告色，弹窗标题也跟着说签退
function isCheckout(row) {
  const c = row.checkin
  return Boolean(c?.open && row.checked_in && !row.checkout_at && row.status === 'enrolled')
}

/** 还要等几分钟才能签退；不在保护窗里就返回 0。
 *
 *  刚签完到又点「签退」的，多半是想确认刚才签上没有。这一下无论调摄像头还是
 *  调定位都是白费——服务端十分钟内不认签退（见 config.CHECKOUT_MIN_GAP_MINUTES），
 *  扫完只会被挡回来告诉你「刚签到」。所以先在这儿算出来，点下去就把话说清楚。 */
function checkoutWait(row) {
  const from = row.checkin?.checkout_from
  if (!from || !row.checked_in || row.checkout_at) return 0
  const left = Math.ceil(
    (new Date(from.replace(' ', 'T')).getTime() - Date.now()) / 60000)
  return left > 0 ? left : 0
}

// 扫还是定，由后端说了算（_checkin_state 的 method）：同一场次里有的岗位
// 现场有码可扫，有的岗位（散在校门口、看台这种）就只能靠定位。
function openCheckin(row) {
  const wait = checkoutWait(row)
  if (wait) return ElMessage.warning(t('刚签到不久，{0} 分钟后才能签退', [wait]))
  if (row.checkin?.method === 'geo') return geoCheckin(row)
  // 这一轮是签到还是签退：弹窗的标题和「正在…」都得跟着走
  scanLabel.value = isCheckout(row) ? '签退' : '签到'
  scanVisible.value = true
}

/** 定位签到/签退：取一次坐标交给服务端比对。

 *  按钮本身不带任何「我到了」的凭据——到没到由服务端拿坐标和锚点算出来，
 *  这里取的坐标只是喂给它的输入。所以手机上的定位权限、精度都还有意义，
 *  但不用指望它防住有心人（见后端 routers/checkin.py 里那段说明）。 */
async function geoCheckin(row) {
  if (geoBusy.value) return
  if (!navigator.geolocation) {
    ElMessage.error(t('这个浏览器不能定位，请联系负责人手动补签'))
    return
  }
  geoBusy.value = true
  const tip = ElMessage({ message: t('正在定位…'), duration: 0 })
  try {
    const pos = await new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true, timeout: 15000, maximumAge: 0,
      })
    })
    const d = await api.post(`/api/checkin/enrollments/${row.id}`, {
      lat: pos.coords.latitude,
      lng: pos.coords.longitude,
      accuracy: pos.coords.accuracy || 0,
    }, { skipErrorToast: true })
    ElMessage.success(d.message)
    await load()
  } catch (e) {
    // 取不到坐标和服务端判定不在范围内是两回事，说法不能混
    ElMessage.error(geoFailText(e))
  } finally {
    tip.close()
    geoBusy.value = false
  }
}

/** 定位失败的原因就在 PositionError.code 里，照它说人话；
 *  其余的是服务端返回的 (api 层已经把 detail 放进 message)。 */
function geoFailText(e) {
  if (typeof e?.code === 'number') {
    if (!window.isSecureContext) return t('定位只能在 https 下使用，请用平台正式网址打开')
    if (e.code === 1) return t('您没有允许定位，无法签到；请在浏览器设置里允许后重试')
    if (e.code === 2) return t('取不到定位，请到空旷处或窗边重试')
    if (e.code === 3) return t('定位超时，请重试')
  }
  return e?.message || t('签到失败')
}

// 成功提示由扫码弹窗自己发（带服务端的原话），这里只管把状态刷成已到岗
async function onChecked() {
  await load()
}

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
// 场次开场后后端不再收退选，按钮也不摆出来
function started(r) {
  return !!r.shift.start_time && new Date(r.shift.start_time.replace(' ', 'T')) <= new Date()
}


async function load() {
  loading.value = true
  try { rows.value = await api.get('/api/me/enrollments') } finally { loading.value = false }
}
onMounted(load)

async function withdraw(row) {
  await ElMessageBox.confirm(t('撤回后可以重新申请。确定撤回吗？'), t('撤回申请'), { type: 'warning' })
  await api.del(`/api/activities/${row.activity.id}/apply`)
  ElMessage.success(t('已撤回申请'))
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
  ElMessage.success(t(d.allow_reassign ? '已设为接受岗位调剂' : '已设为不接受岗位调剂'))
}

async function cancel(row) {
  // 锁定状态按时刻算（后端 position_locked）：页面可能是几分钟前打开的，
  // 跨过自动锁定时刻后那句「退出会被记录一次」就漏了。点之前重取一次
  let fresh = row
  try {
    const list = await api.get('/api/me/enrollments')
    fresh = list.find(r => r.id === row.id) || row
  } catch { /* 取不到就按页面上这份算，服务端仍会按锁后的规矩处理 */ }
  const locked = fresh.status === 'enrolled' && fresh.list_locked
  await ElMessageBox.confirm(
    t(locked ? '名单已锁定，此时退出会被记录一次。确定退出吗？' : '确定退选吗？'),
    t('确认退选'), { type: 'warning' })
  const d = await api.del(`/api/activities/enrollments/${row.id}`)
  ElMessage[d.effect === 'locked' ? 'warning' : 'success'](d.message)
  load()
}
</script>

<style scoped>
.mob-list { display: flex; flex-direction: column; gap: 12px; }
.mob-card { background:#fff; border:1px solid #eceef1; border-radius:4px; padding:12px 14px; }
.mob-head { display:flex; justify-content:space-between; gap:8px; align-items:flex-start;
            margin-bottom:4px; }
.mob-head b { font-size:14.5px; line-height:1.45; }
.mob-line { margin-top:8px; font-size:13px; display:flex; align-items:center;
            gap:4px; flex-wrap:wrap; }
.mob-actions { margin-top:12px; padding-top:10px; border-top:1px dashed #eceef1;
               display:flex; gap:8px; flex-wrap:wrap; }
.title { color: var(--el-color-primary); cursor: pointer; }
.checkout-time {
  font-size: 11px;
  line-height: 1.4;
  margin-top: 4px;
}
</style>

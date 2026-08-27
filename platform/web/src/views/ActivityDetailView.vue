<template>
  <div class="page" v-if="act">
    <el-page-header @back="$router.back()" :title="'返回'">
      <template #content>
        <span style="font-weight:600">{{ act.title }}</span>
        <el-tag :color="typeColor(act.type)" effect="dark" style="border:none;margin-left:10px">
          {{ act.type_label }}
        </el-tag>
        <el-tag style="margin-left:6px" :type="statusType">{{ act.status_label }}</el-tag>
      </template>
    </el-page-header>

    <el-row :gutter="18" style="margin-top:16px">
      <el-col :span="17">
        <el-card shadow="never">
          <h3 style="margin-top:0">{{ act.title }}</h3>
          <p style="white-space:pre-wrap;color:#606266;line-height:1.8">{{ act.intro }}</p>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="整体时间">{{ act.time_text }}</el-descriptions-item>
            <el-descriptions-item label="活动地点">{{ act.location || '待定' }}</el-descriptions-item>
            <el-descriptions-item label="公示联系方式">{{ act.contact_info || '—' }}</el-descriptions-item>
            <el-descriptions-item label="报名窗口">
              {{ fmtDT(act.reg_start) }} ~ {{ fmtDT(act.reg_end) }}
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="act.qa_group_qr" style="margin-top:10px;display:flex;gap:10px;align-items:center">
            <img :src="act.qa_group_qr" width="86" height="86"
                 style="border-radius:8px;border:1px solid #eceef1" />
            <span class="dim">扫码入群，自愿加入<br />（平台仅作为二维码展示容器）</span>
          </div>
        </el-card>

        <div class="section-title">岗位安排<span class="dim" style="font-weight:400;margin-left:8px">
          点击岗位名称可查看详情；点击行首箭头展开岗位</span></div>

        <el-card shadow="never">
          <el-table :data="act.shifts" row-key="id"
                    :row-class-name="({ row }) => row.mode === 'pick' ? 'no-expand-row' : ''">
            <el-table-column type="expand" width="34">
              <template #default="{ row: g }">
                <div style="padding:6px 12px 6px 40px">
                  <!-- 直接定二级：岗位表格 -->
                  <template v-if="g.mode === 'pick' && g.positions.length">
                    <el-table :data="g.positions" size="small" border>
                      <el-table-column label="岗位" min-width="150">
                        <template #default="{ row: s }">
                          <el-link type="primary" :underline="false" style="font-weight:600"
                                   @click="showStationDetail(g, s)">{{ s.name }}</el-link>
                          <el-tag v-if="myStationId(g) === s.id" size="small" type="success"
                                  effect="light" style="margin-left:6px">已选</el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="location" label="服务地点" min-width="110" />
                      <el-table-column label="名额" width="100">
                        <template #default="{ row: s }">
                          {{ s.confirmed }}/{{ s.quota }}
                          <el-tag v-if="s.remaining <= 0" size="small" type="danger"
                                  effect="plain">满</el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="requirement_desc" label="要求" min-width="110" />
                      <el-table-column label="操作" width="170" align="center">
                        <template #default="{ row: s }">
                          <!-- 我选中的岗位：原地退选/确认 -->
                          <template v-if="g.my_enrollment && myStationId(g) === s.id">
                            <template v-if="g.my_enrollment.status === 'pending_confirm'">
                              <el-countdown format="HH:mm:ss" :value="deadlineTs(g)"
                                            style="vertical-align:middle;margin-right:6px" />
                              <el-button type="primary" size="small" @click="confirmPromo(g)">确认</el-button>
                              <el-button size="small" plain @click="cancelEnroll(g)">放弃</el-button>
                            </template>
                            <el-button v-else size="small" type="danger" plain
                                       :disabled="!canCancel(g)" @click="cancelEnroll(g)">
                              {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                            </el-button>
                          </template>
                          <!-- 已选其他岗位：置灰 -->
                          <el-tooltip v-else-if="g.my_enrollment"
                                      content="同场次只能选择一个岗位；如需更换请先退选" placement="top">
                            <el-button size="small" disabled>
                              {{ s.remaining > 0 ? '选中' : '候补' }}
                            </el-button>
                          </el-tooltip>
                          <!-- 未报名 -->
                          <el-button v-else-if="act.status === 'published'" size="small"
                                     type="primary" plain @click="doEnroll(g, s)">
                            {{ s.remaining > 0 ? '选中' : '候补' }}
                          </el-button>
                          <span v-else class="dim">—</span>
                        </template>
                      </el-table-column>
                    </el-table>
                    <div class="dim" style="margin-top:6px">
                      同一场次内只能选择一个岗位：选中后在本行退选，其他岗位按钮置灰；
                      岗位满员可点「候补」，按排队顺序自动递补（转正需2小时内确认）。
                    </div>
                  </template>
                  <p v-else-if="g.mode === 'assign'" class="dim" style="margin:4px 0">
                    {{ g.note || '本场次为「二级待定」：报名只锁定时段，报名截止后由青协通过排班程序统一分岗位，结果经企业微信通知。' }}
                  </p>
                  <p v-else class="dim" style="margin:4px 0">该场次为「仅场次」，无需选择岗位，报名即锁定时段。</p>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="场次" min-width="180">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" style="font-weight:600"
                         @click="showGroupDetail(row)">{{ row.name }}</el-link>
                <el-tag size="small" effect="plain" style="margin-left:6px"
                        :type="row.mode === 'pick' ? 'info' : row.mode === 'assign' ? 'warning' : 'primary'">
                  {{ row.mode_label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="时间" min-width="140" prop="time_text" />
            <el-table-column label="已确认/名额" min-width="150">
              <template #default="{ row }">
                <div>{{ row.confirmed }}/{{ row.capacity }}
                  <span v-if="row.waitlist_count" class="dim">· 候补 {{ row.waitlist_count }}</span>
                </div>
                <el-progress :percentage="pct(row)" :stroke-width="5" :show-text="false" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="230" align="right">
              <template #default="{ row }">
                <template v-if="row.my_enrollment">
                  <!-- 调剂意愿：点一下即可改，只作为负责老师排班时的参考 -->
                  <div style="margin-bottom:4px">
                    <el-tooltip content="点击切换。仅供负责老师参考，不代表岗位不会被调整"
                                placement="top">
                      <el-tag size="small" effect="plain" style="cursor:pointer"
                              :type="row.my_enrollment.allow_reassign ? 'success' : 'info'"
                              @click="toggleReassign(row)">
                        {{ row.my_enrollment.allow_reassign ? '接受调剂' : '不接受调剂' }}
                      </el-tag>
                    </el-tooltip>
                  </div>
                  <!-- 直接定二级 -->
                  <template v-if="row.mode === 'pick'">
                    <!-- 有岗位：状态标签（退选/确认在对应岗位行原地操作） -->
                    <template v-if="myStationId(row)">
                      <el-tag v-if="row.my_enrollment.status === 'confirmed'" type="success">
                        已选中{{ myStationName(row) ? '·' + myStationName(row) : '' }}
                      </el-tag>
                      <el-tag v-else-if="row.my_enrollment.status === 'waitlisted'" type="warning">
                        候补中 · 第 {{ row.my_enrollment.waitlist_position }} 位
                      </el-tag>
                      <template v-else>
                        <el-tag type="danger">转正确认剩</el-tag>&nbsp;
                        <el-countdown format="HH:mm:ss" :value="deadlineTs(row)"
                                      style="vertical-align:middle" />
                        <span class="dim" style="margin-left:6px">在对应岗位行确认</span>
                      </template>
                    </template>
                    <!-- 无岗位（管理员补录/历史数据）：操作留在场次行 -->
                    <template v-else>
                      <el-tag v-if="row.my_enrollment.status === 'confirmed'" type="success">
                        已选中（岗位待分配）
                      </el-tag>
                      <el-tag v-else-if="row.my_enrollment.status === 'waitlisted'" type="warning">
                        候补中 · 第 {{ row.my_enrollment.waitlist_position }} 位
                      </el-tag>
                      <template v-else>
                        <el-countdown format="HH:mm:ss" :value="deadlineTs(row)"
                                      style="vertical-align:middle;margin-right:8px">
                          <template #prefix>
                            <el-tag type="danger" size="small">转正确认剩</el-tag>&nbsp;
                          </template>
                        </el-countdown>
                        <el-button type="primary" size="small"
                                   @click="confirmPromo(row)">确认转正</el-button>
                      </template>
                      <el-button v-if="row.my_enrollment.status === 'confirmed'"
                                 size="small" type="danger" plain
                                 :disabled="!canCancel(row)" @click="cancelEnroll(row)">
                        退选
                      </el-button>
                      <el-button v-else-if="row.my_enrollment.status === 'waitlisted'"
                                 size="small" type="danger" plain @click="cancelEnroll(row)">
                        退出候补
                      </el-button>
                    </template>
                  </template>
                  <!-- 仅一级 / 二级待定：按钮留在场次行 -->
                  <template v-else>
                    <el-tag v-if="row.my_enrollment.status === 'confirmed'" type="success">
                      已选中
                    </el-tag>
                    <el-tag v-else-if="row.my_enrollment.status === 'waitlisted'" type="warning">
                      候补中 · 第 {{ row.my_enrollment.waitlist_position }} 位
                    </el-tag>
                    <template v-else>
                      <el-countdown format="HH:mm:ss" :value="deadlineTs(row)"
                                    style="vertical-align:middle;margin-right:8px">
                        <template #prefix>
                          <el-tag type="danger" size="small">转正确认剩</el-tag>&nbsp;
                        </template>
                      </el-countdown>
                      <el-button type="primary" size="small"
                                 @click="confirmPromo(row)">确认转正</el-button>
                    </template>
                    <el-button v-if="row.my_enrollment.status === 'confirmed'"
                               size="small" type="danger" plain
                               :disabled="!canCancel(row)" @click="cancelEnroll(row)">
                      退选
                    </el-button>
                    <el-button v-else-if="row.my_enrollment.status === 'waitlisted'"
                               size="small" type="danger" plain @click="cancelEnroll(row)">
                      退出候补
                    </el-button>
                  </template>
                </template>
                <template v-else-if="act.status === 'published'">
                  <el-tooltip v-if="row.mode === 'pick'"
                              content="点击行首箭头展开，选择一个岗位" placement="top">
                    <el-button size="small" disabled>展开选择岗位</el-button>
                  </el-tooltip>
                  <el-button v-else type="primary" size="small" @click="doEnroll(row, null)">
                    {{ enrollText(row) }}
                  </el-button>
                </template>
                <el-tag v-else type="info" size="small">不在报名期</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 右：冲突提示 / 变更记录 -->
      <el-col :span="7">
        <el-alert v-if="act.my_time_conflicts?.length" type="error" :closable="false"
                  style="margin-bottom:14px">
          <p style="margin:0 0 4px"><b>您存在时段重叠的既有报名：</b></p>
          <p v-for="c in act.my_time_conflicts" :key="c.enrollment_id" style="margin:2px 0;font-size:12px">
            「{{ c.activity_title }}」{{ c.shift_name }}（{{ c.time }}）
          </p>
        </el-alert>
        <el-card shadow="never">
          <template #header><b>变更记录</b><span class="dim" style="font-weight:400;margin-left:6px">公开透明 · 全程留痕</span></template>
          <el-timeline v-if="act.change_logs?.length" style="padding-left:2px">
            <el-timeline-item v-for="l in act.change_logs" :key="l.id"
                              :timestamp="fmtDT(l.created_at)" placement="top"
                              :type="l.action === 'delete' ? 'danger' : 'primary'">
              <b style="font-size:13px">{{ l.actor_name }}</b>
              <span class="dim"> {{ l.summary }}</span>
              <div v-for="(ch, i) in l.changes" :key="i" class="dim" style="margin-top:2px">
                {{ ch.label }}：{{ ch.old || '空' }} → <b style="color:var(--el-color-primary)">{{ ch.new || '空' }}</b>
              </div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无变更" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 场次详情 -->
    <el-dialog v-model="groupDetail.visible" :title="(groupDetail.data?.name || '') + ' · 场次详情'" width="480">
      <el-descriptions v-if="groupDetail.data" :column="1" border>
        <el-descriptions-item label="岗位模式">{{ groupDetail.data.mode_label }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ groupDetail.data.time_text }}</el-descriptions-item>
        <el-descriptions-item label="人数">
          已确认 {{ groupDetail.data.confirmed }} / {{ groupDetail.data.capacity }}
          <span v-if="groupDetail.data.waitlist_count" class="dim">（候补 {{ groupDetail.data.waitlist_count }} 人）</span>
          <span v-if="groupDetail.data.held_seats" class="dim">
            · 另有 {{ groupDetail.data.held_seats }} 个名额因临近退选被扣留，不再释放</span>
        </el-descriptions-item>
        <el-descriptions-item label="剩余名额">{{ groupDetail.data.remaining }}</el-descriptions-item>
        <el-descriptions-item label="说明">{{ groupDetail.data.note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 岗位详情 -->
    <el-dialog v-model="stationDetail.visible" :title="(stationDetail.data?.name || '') + ' · 岗位详情'" width="480">
      <el-descriptions v-if="stationDetail.data" :column="1" border>
        <el-descriptions-item label="所属场次">{{ stationDetail.groupName }}（{{ stationDetail.groupTime }}）</el-descriptions-item>
        <el-descriptions-item label="服务地点">{{ stationDetail.data.location || '待定' }}</el-descriptions-item>
        <el-descriptions-item label="名额">
          {{ stationDetail.data.confirmed }}/{{ stationDetail.data.quota }}
          <span class="dim">（岗位之间互斥，一场次一人一个岗位）</span>
        </el-descriptions-item>
        <el-descriptions-item label="条件要求">{{ stationDetail.data.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item label="要求说明">{{ stationDetail.data.requirement_note || '—' }}</el-descriptions-item>
        <el-descriptions-item label="培训内容">{{ stationDetail.data.training_note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 报名确认：确认时段 + 表态是否接受岗位调剂 -->
    <el-dialog v-model="enrollDlg.visible" title="确认报名" width="480px">
      <template v-if="enrollDlg.group">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="场次">
            {{ enrollDlg.group.name }}（{{ enrollDlg.group.time_text }}）
          </el-descriptions-item>
          <el-descriptions-item label="岗位">
            {{ enrollDlg.position ? enrollDlg.position.name
               : (enrollDlg.group.mode === 'assign' ? '报名截止后由青协统一分配' : '无需选择') }}
          </el-descriptions-item>
          <el-descriptions-item label="结果">
            <el-tag :type="willWaitlist ? 'warning' : 'success'" size="small">
              {{ willWaitlist ? '名额已满，将加入候补队列' : '名额充足，报名即生效' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div class="reassign-box">
          <el-checkbox v-model="enrollDlg.allowReassign">接受岗位调剂</el-checkbox>
          <p class="dim">
            勾选表示你愿意被安排到本场次的其他岗位。这只是给负责老师的参考——
            实际排班仍由他们决定，不勾选也可能被调整，但会在名单上标注你的意愿。
          </p>
        </div>

        <el-alert v-if="needLateWarn(enrollDlg.group)" type="warning" :closable="false"
                  style="margin-top:10px"
                  title="距该场次开始已不足 24 小时：报名后再退选将扣除信用分，且名额不再释放" />
      </template>
      <template #footer>
        <el-button @click="enrollDlg.visible = false">再想想</el-button>
        <el-button type="primary" :loading="enrollDlg.submitting" @click="submitEnroll">
          确认{{ willWaitlist ? '加入候补' : '报名' }}
        </el-button>
      </template>
    </el-dialog>

    <ProfileDialog ref="profileDialog" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT } from '../api'
import { TYPE_COLORS } from '../store'
import ProfileDialog from '../components/ProfileDialog.vue'

const route = useRoute()
const act = ref(null)
const groupDetail = ref({ visible: false, data: null })
const enrollDlg = ref({ visible: false, group: null, position: null,
                        allowReassign: true, submitting: false })
const willWaitlist = computed(() => {
  const { group: g, position } = enrollDlg.value
  if (!g) return false
  return position ? position.remaining <= 0 : g.remaining <= 0
})
const stationDetail = ref({ visible: false, data: null, groupName: '', groupTime: '' })

const typeColor = t => TYPE_COLORS[t]?.color
const statusType = { published: 'success', closed: 'info', draft: 'warning', finished: 'info' }

function pct(g) {
  return Math.min(100, Math.round((g.confirmed / Math.max(1, g.capacity)) * 100))
}
function needLateWarn(g) {
  const st = new Date(g.start_time.replace(' ', 'T'))
  return Date.now() > st.getTime() - 24 * 3600 * 1000
}
function deadlineTs(g) {
  return new Date(g.my_enrollment.confirm_deadline.replace(' ', 'T').slice(0, 19)).getTime()
}
function myStationId(g) {
  return g.my_enrollment?.position_id || ''
}
function myStationName(g) {
  const sid = myStationId(g)
  if (!sid) return ''
  for (const s of g.positions) if (s.id === sid) return s.name
  return ''
}
function canCancel(g) {
  return new Date(g.start_time.replace(' ', 'T')) > new Date()
}
function enrollText(g) {
  if (g.mode === 'pick') {
    return g.remaining > 0 ? '选中' : '加入候补'
  }
  return g.remaining > 0 ? '立即报名' : '加入候补'
}
function showGroupDetail(g) {
  groupDetail.value = { visible: true, data: g }
}
function showStationDetail(g, s) {
  stationDetail.value = { visible: true, data: s,
                          groupName: g.name, groupTime: g.time_text }
}

async function load() {
  act.value = await api.get(`/api/activities/${route.params.id}`)
}
onMounted(load)

function doEnroll(g, position) {
  if (g.mode === 'pick' && g.positions.length && !position) {
    return ElMessage.warning('请先展开该场次，选择一个岗位')
  }
  enrollDlg.value = { visible: true, group: g, position, allowReassign: true,
                      submitting: false }
}

async function submitEnroll() {
  const { group: g, position, allowReassign } = enrollDlg.value
  enrollDlg.value.submitting = true
  try {
    const d = await api.post(
      `/api/activities/${act.value.id}/groups/${g.id}/enroll`,
      { position_id: position ? position.id : null, allow_reassign: allowReassign })
    enrollDlg.value.visible = false
    ElMessage.success(d.message)
    load()
  } catch (e) {
    // 资料未完善被拦截：提示后弹出资料表单，补全后即可报名
    if (e?.message?.includes('完善个人信息')) {
      enrollDlg.value.visible = false
      try {
        await ElMessageBox.alert(
          '报名前需先完善个人信息（手机号/微信号/身份证号），请在接下来的弹窗中填写并保存。',
          '需先完善个人信息',
          { type: 'warning', confirmButtonText: '去完善' })
      } catch { /* 关闭提示也照样打开表单 */ }
      openProfileDialog()
      return
    }
    if (e.data && e.data.conflicts) {
      enrollDlg.value.visible = false
      const list = e.data.conflicts
        .map(c => `「${c.activity_title}」${c.shift_name}（${c.time}）`
                  + (c.waiting ? '　<i>候补中</i>' : '')).join('<br>')
      ElMessageBox.alert(list, '与已有报名时间冲突',
                         { dangerouslyUseHTMLString: true, type: 'error' })
    }
  } finally {
    enrollDlg.value.submitting = false
  }
}

// 调剂意愿随时可改：它只是给负责老师的参考
async function toggleReassign(g) {
  const enr = g.my_enrollment
  const d = await api.patch(
    `/api/activities/enrollments/${enr.enrollment_id}/reassign-pref`,
    { allow_reassign: !enr.allow_reassign })
  enr.allow_reassign = d.allow_reassign
  ElMessage.success(d.allow_reassign ? '已设为接受岗位调剂' : '已设为不接受岗位调剂')
}

async function cancelEnroll(g) {
  const enrId = g.my_enrollment.enrollment_id
  const late = !canCancel(g)
  try {
    await ElMessageBox.confirm(
      late ? '距开始不足24小时：退选将扣除信用分5分，且该名额会被扣留、不再释放给任何人，'
             + '你本人之后也无法再报名这个场次。确定退选吗？'
           : '正常退选不扣信用分，名额将按候补顺序自动递补。',
      '确认退选', { type: 'warning', confirmButtonText: '确定退选', cancelButtonText: '取消' })
  } catch { return }
  const d = await api.del(`/api/activities/enrollments/${enrId}`)
  ElMessage[d.effect === 'penalized' ? 'warning' : 'success'](d.message)
  load()
}

async function confirmPromo(g) {
  const d = await api.post(
    `/api/activities/enrollments/${g.my_enrollment.enrollment_id}/confirm-promotion`)
  ElMessage.success(d.message)
  load()
}

const profileDialog = ref(null)
const openProfileDialog = () => profileDialog.value.open(true)
</script>

<style scoped>
.group-card { margin-bottom: 14px; border-radius: 12px; }
.g-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.reassign-box { margin-top: 14px; padding: 10px 12px; border-radius: 8px; background: #f7f8fa; }
.reassign-box .dim { margin: 6px 0 0; font-size: 12px; line-height: 1.7; }

/* 仅场次：没有可展开的岗位，隐藏展开箭头 */
:deep(.no-expand-row .el-table__expand-icon) { visibility: hidden; }
</style>

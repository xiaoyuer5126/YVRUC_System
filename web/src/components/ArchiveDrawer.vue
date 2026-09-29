<!-- 志愿者档案抽屉。全校名册页与活动名单页都用它，只是拿数据的接口不同。 -->
<template>
  <el-drawer v-model="visible" :size="narrow ? '100%' : '560px'"
             :title="d ? `${d.user.name} 的志愿档案` : '志愿档案'">
    <div v-if="d" v-loading="loading">
      <el-descriptions :column="narrow ? 1 : 2" size="small" border>
        <el-descriptions-item label="学号">{{ d.user.student_id }}</el-descriptions-item>
        <el-descriptions-item label="学院">{{ d.user.college || '—' }}</el-descriptions-item>
        <el-descriptions-item label="校区 / 学段">{{ d.user.campus || '—' }} / {{ d.user.stage || '—' }}</el-descriptions-item>
        <el-descriptions-item label="手机号">{{ d.user.phone || '—' }}</el-descriptions-item>
        <el-descriptions-item label="微信号"><span class="mono">{{ d.user.wechat || '—' }}</span></el-descriptions-item>
        <el-descriptions-item label="身份证"><span class="mono dim">{{ d.user.id_card_masked || '—' }}</span></el-descriptions-item>
      </el-descriptions>

      <div class="stats">
        <div class="stat"><b>{{ served }}</b><span class="dim">次到岗</span></div>
        <div class="stat"><b>{{ d.history.length }}</b><span class="dim">次报名</span></div>
        <div class="stat" :class="{ warn: d.late_cancel_count }">
          <b>{{ d.late_cancel_count }}</b><span class="dim">次临期退选</span>
        </div>
        <div class="stat" :class="{ warn: d.cancels.recent_count >= 3 }">
          <b>{{ d.cancels.recent_count }}</b>
          <span class="dim">近 {{ d.cancels.window_days }} 天退选</span>
        </div>
        <div class="stat" :class="{ warn: d.violations.length }">
          <b>{{ d.violations.length }}</b><span class="dim">条违规记录</span>
        </div>
        <div class="stat" :class="{ warn: d.score.score_alert }">
          <b>{{ d.score.score }}</b><span class="dim">内部提示分</span>
        </div>
      </div>

      <!-- 提示分只给青协内部看：不公开给志愿者，也不拦任何人报名 -->
      <div class="section-title">提示分明细</div>
      <!-- 按时间逐条累计，每一步夹在 0～满分之间：满分时加的分不存起来 -->
      <div class="score-items">
        <div class="score-item dim">起始 <b class="val">{{ d.score.base }}</b></div>
        <div v-for="(it, i) in d.score.items" :key="i" class="score-item">
          <span class="dim">{{ fmtDT(it.at) }}</span>
          <span class="what">{{ it.label }} · {{ it.activity_title }}</span>
          <b :class="{ minus: it.delta < 0 }">{{ it.delta > 0 ? '+' : '' }}{{ it.delta }}</b>
          <b class="val">{{ it.score }}</b>
        </div>
      </div>
      <p class="dim" style="margin:4px 0 0">
        低于 {{ d.score.alert_below }} 分时名单上会标出提示。每个签到过、无违规也无临期退选的活动，
        结束后加 {{ d.score.per_activity }} 分，满分封顶不累积。不对志愿者公开，也不影响报名。
      </p>

      <!-- 退选画像：次数与「报了多久才退」，判断是临时有事还是占了就退 -->
      <div class="section-title">
        退选记录
        <span v-if="d.cancels.median_gap_hours !== null" class="dim">
          （共 {{ d.cancels.total_count }} 次，报名到退选间隔中位数
          {{ gapText(d.cancels.median_gap_hours) }}）
        </span>
      </div>
      <el-empty v-if="!d.cancels.items.length" :image-size="50" description="没有退选记录" />
      <DataCards v-else-if="narrow" :rows="d.cancels.items" title-key="activity_title"
                 :fields="[{ key: 'cancelled_at', label: '退选时间', format: fmtDT },
                           { key: 'gap_hours', label: '报名后多久', format: gapText }]">
        <template #title="{ row }">{{ row.activity_title }} · {{ row.shift_name }}</template>
        <template #extra="{ row }">
          <el-tag size="small" effect="plain"
                  :type="row.cancel_type === 'late' ? 'danger' : 'info'">
            {{ CANCEL_LABELS[row.cancel_type] || row.cancel_type }}
          </el-tag>
        </template>
      </DataCards>
      <el-table v-else :data="d.cancels.items" size="small">
        <el-table-column label="活动" min-width="150">
          <template #default="{ row }">{{ row.activity_title }} · {{ row.shift_name }}</template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain"
                    :type="row.cancel_type === 'late' ? 'danger' : 'info'">
              {{ CANCEL_LABELS[row.cancel_type] || row.cancel_type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="退选时间" width="130">
          <template #default="{ row }">{{ row.cancelled_at ? fmtDT(row.cancelled_at) : '—' }}</template>
        </el-table-column>
        <el-table-column label="报名后多久" width="110">
          <template #default="{ row }">{{ gapText(row.gap_hours) }}</template>
        </el-table-column>
      </el-table>

      <div class="section-title">违规记录</div>
      <el-empty v-if="!d.violations.length" :image-size="50" description="没有违规记录" />
      <div v-for="v in d.violations" :key="v.id" class="vio">
        <el-tag size="small" type="warning" effect="plain">{{ v.kind_label }}</el-tag>
        <b>{{ v.activity_title }}</b>
        <span class="dim">{{ fmtDT(v.created_at) }}</span>
        <p>{{ v.detail }}</p>
      </div>

      <div class="section-title">参与过的活动</div>
      <el-empty v-if="!d.history.length" :image-size="50" description="还没有报过名" />
      <DataCards v-else-if="narrow" :rows="d.history" row-key="enrollment_id"
                 :fields="[{ key: 'time_text', label: '时间' }]">
        <template #title="{ row }">{{ row.activity_title }} · {{ row.shift_name }}</template>
        <template #extra="{ row }">
          <el-tag size="small" :type="tagType(row)" effect="plain">{{ tagText(row) }}</el-tag>
        </template>
      </DataCards>
      <el-table v-else :data="d.history" size="small">
        <el-table-column label="活动" min-width="160">
          <template #default="{ row }">{{ row.activity_title }} · {{ row.shift_name }}</template>
        </el-table-column>
        <el-table-column prop="time_text" label="时间" width="150" />
        <el-table-column label="结果" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row)" effect="plain">{{ tagText(row) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from './DataCards.vue'

const narrow = useNarrow()
const visible = ref(false)
const loading = ref(false)
const d = ref(null)

const served = computed(() => d.value ? d.value.history.filter(h => h.checked_in).length : 0)

const CANCEL_LABELS = { normal: '退选', late: '临期退选', expired: '确认超时',
                        abandoned: '放弃候补' }
// 报名到退选的间隔：几小时的看小时，长了看天，太长了没必要精确
function gapText(h) {
  if (h === null || h === undefined) return '—'
  if (h < 48) return `${h} 小时`
  return `${Math.round(h / 24)} 天`
}

async function open(url) {
  visible.value = true
  loading.value = true
  d.value = null
  try { d.value = await api.get(url) } finally { loading.value = false }
}
defineExpose({ open })

function tagText(h) {
  if (h.status === 'enrolled') return h.checked_in ? '已到岗' : '已报名'
  if (h.status === 'waitlisted' || h.status === 'pending_confirm') return '候补中'
  return { late: '临期退选', normal: '已退选', expired: '确认超时',
           abandoned: '放弃候补', shift_deleted: '场次取消' }[h.cancel_type] || '已退选'
}
function tagType(h) {
  if (h.status === 'enrolled') return h.checked_in ? 'success' : 'primary'
  if (h.cancel_type === 'late') return 'warning'
  return 'info'
}
</script>

<style scoped>
.score-items { font-size: 13px; }
.score-item { display: flex; gap: 8px; padding: 4px 0; align-items: baseline; }
.score-item .what { flex: 1; min-width: 0; }
.score-item b { color: var(--el-color-success); }
.score-item b.minus { color: var(--el-color-danger); }
.score-item b.val { color: inherit; min-width: 28px; text-align: right; }
.stats { display: flex; gap: 12px; margin: 12px 0; }
.stat { flex: 1; text-align: center; padding: 10px 6px; border-radius: 10px;
        background: var(--el-fill-color-light); }
/* 六个格子横排在手机上每个只剩五十来像素，数字和说明都挤成两行 */
@media (max-width: 860px) {
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); }
}
.stat b { display: block; font-size: 20px; }
.stat.warn b { color: var(--el-color-warning); }
.vio { padding: 8px 0; border-bottom: 1px dashed var(--el-border-color); }
.vio b { margin: 0 8px; }
.vio p { margin: 4px 0 0; color: #606266; font-size: 13px; }
.mono { font-family: ui-monospace, monospace; }
</style>

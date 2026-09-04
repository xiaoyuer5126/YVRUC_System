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
          <b>{{ d.late_cancel_count }}</b><span class="dim">次临近退选</span>
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
      <div class="score-items">
        <span v-for="(it, i) in d.score.items" :key="i" class="score-item">
          {{ it.label }}<template v-if="it.count > 1"> ×{{ it.count }}</template>
          <b :class="{ minus: it.delta < 0 }">{{ it.delta > 0 ? '+' : '' }}{{ it.delta }}</b>
        </span>
      </div>
      <p class="dim" style="margin:4px 0 0">
        低于 {{ d.score.alert_below }} 分时名单上会挂感叹号，提醒排班的人看一眼这一页。
        顺利参与一次活动加 {{ perActivity }} 分，分数会自己长回来。
        不对志愿者公开，也不影响报名。
      </p>

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

const narrow = useNarrow()
const visible = ref(false)
const loading = ref(false)
const d = ref(null)

const served = computed(() => d.value ? d.value.history.filter(h => h.checked_in).length : 0)
// 加分那一项就是「顺利参与一次」的权重，直接从明细里取，别在前端抄第二份
const perActivity = computed(
  () => d.value?.score.items.find(i => i.label === '顺利参与活动')?.delta
        / (d.value?.score.items.find(i => i.label === '顺利参与活动')?.count || 1) || 5)

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
  return { late: '临近退选', normal: '已退选', expired: '确认超时',
           abandoned: '放弃候补', shift_deleted: '场次取消' }[h.cancel_type] || '已退选'
}
function tagType(h) {
  if (h.status === 'enrolled') return h.checked_in ? 'success' : 'primary'
  if (h.cancel_type === 'late') return 'warning'
  return 'info'
}
</script>

<style scoped>
.score-items { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 13px; }
.score-item b { margin-left: 4px; color: var(--el-color-success); }
.score-item b.minus { color: var(--el-color-danger); }
.stats { display: flex; gap: 10px; margin: 14px 0; }
.stat { flex: 1; text-align: center; padding: 10px 6px; border-radius: 10px;
        background: var(--el-fill-color-light); }
.stat b { display: block; font-size: 20px; }
.stat.warn b { color: var(--el-color-warning); }
.vio { padding: 8px 0; border-bottom: 1px dashed var(--el-border-color); }
.vio b { margin: 0 8px; }
.vio p { margin: 4px 0 0; color: #606266; font-size: 13px; }
.mono { font-family: ui-monospace, monospace; }
</style>

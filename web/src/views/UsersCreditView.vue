<template>
  <div class="page page-wide">
    <h2 style="margin-top:0">用户与信用管理</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="q" placeholder="姓名 / 学号" clearable style="width:220px"
                  @keyup.enter="load" @clear="load">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="college" clearable placeholder="学院筛选" style="width:180px" @change="load">
          <el-option v-for="c in colleges" :key="c" :value="c" :label="c" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
        <span class="dim">信用分低于 60 自动冻结报名 30 天；限制准入依据《志愿者服务规范》</span>
      </div>

      <DataCards v-if="narrow" :rows="users" title-key="name" v-loading="loading"
                 :fields="[{ key: 'student_id', label: '学号' },
                           { key: 'wechat', label: '微信号', hideEmpty: true },
                           { key: 'college', label: '学院' },
                           { key: 'phone', label: '手机号' },
                           { key: 'id_card_masked', label: '证件号', hideEmpty: true }]"
                 empty="没有匹配的志愿者">
        <template #extra="{ row }">
          <el-tag :type="row.credit_score >= 60 ? 'success' : 'danger'" effect="light">
            {{ row.credit_score }} 分
          </el-tag>
        </template>
        <template #body="{ row }">
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <el-tag v-if="row.credit_frozen" size="small" type="danger">报名冻结</el-tag>
            <el-tag v-if="row.waitlist_banned" size="small" type="danger" effect="plain">候补暂停</el-tag>
            <el-tag v-if="row.abandon_count" size="small" effect="plain">
              放弃候补 {{ row.abandon_count }} 次
            </el-tag>
            <el-tag v-if="row.restricted_types?.length" size="small" type="warning" effect="plain">
              限 {{ row.restricted_types.join('/') }} 类
            </el-tag>
          </div>
        </template>
        <template #actions="{ row }">
          <el-button size="small" type="primary" plain @click="openCredit(row)">调整信用</el-button>
          <el-button size="small" type="danger" plain @click="openViolation(row)">登记违规</el-button>
          <el-button size="small" @click="openRestrict(row)">限制准入</el-button>
        </template>
      </DataCards>

      <el-table v-else :data="users" size="small" v-loading="loading" max-height="620">
        <el-table-column prop="name" label="姓名" width="90" />
        <el-table-column prop="student_id" label="学号" width="110" />
        <el-table-column prop="gender" label="性别" width="60" />
        <el-table-column prop="college" label="学院" min-width="120" />
        <el-table-column prop="phone" label="手机号" width="120" />
        <el-table-column label="微信号" width="120">
          <template #default="{row}">
            <span class="mono">{{ row.wechat || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="id_card_masked" label="身份证（脱敏）" width="146">
          <template #default="{row}"><span class="mono dim">{{ row.id_card_masked || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="信用分" width="73">
          <template #default="{row}">
            <b :style="{ color: row.credit_score >= 60 ? '#67c23a' : '#f56c6c' }">{{ row.credit_score }}</b>
            <el-tooltip v-if="row.credit_frozen" content="报名已冻结"><el-icon color="#f56c6c"><Lock /></el-icon></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="候补/准入" min-width="150">
          <template #default="{row}">
            <span v-if="row.abandon_count" class="dim">放弃{{ row.abandon_count }}次 </span>
            <el-tag v-if="row.waitlist_banned" size="small" type="danger" effect="plain">候补暂停</el-tag>
            <el-tag v-if="row.restricted_types?.length" size="small" type="warning" effect="plain">
              限{{ row.restricted_types.join('/') }}类
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="205" fixed="right">
          <template #default="{row}">
            <!-- nowrap：三个按钮必须在一行，列宽也要够，否则挤成三行 -->
            <div class="ops">
              <el-button size="small" link type="primary"
                         @click="openCredit(row)">调整信用</el-button>
              <el-button size="small" link type="danger"
                         @click="openViolation(row)">登记违规</el-button>
              <el-button size="small" link @click="openRestrict(row)">限制准入</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 信用调整 -->
    <el-dialog v-model="creditDlg.visible" title="调整信用分" width="420">
      <p>对象：<b>{{ creditDlg.user?.name }}</b>（当前 {{ creditDlg.user?.credit_score }} 分）</p>
      <el-form label-width="80px">
        <el-form-item label="分值变化">
          <el-input-number v-model="creditForm.delta" :min="-100" :max="100" />
        </el-form-item>
        <el-form-item label="事由" required>
          <el-input v-model="creditForm.reason" placeholder="如：C类活动临场消极志愿" />
        </el-form-item>
      </el-form>
      <div class="dim">低于60分将自动触发30天报名冻结并推送企业微信提醒；本操作计入审计日志。</div>
      <template #footer>
        <el-button @click="creditDlg.visible = false">取消</el-button>
        <el-button type="primary" @click="saveCredit">确认调整</el-button>
      </template>
    </el-dialog>

    <!-- 违规登记：按《规范》算建议，人工确认后生效 -->
    <el-dialog v-model="vioDlg.visible" title="登记违规（《志愿者服务规范》限制准入名单）"
               width="620" @closed="vio.suggest = null">
      <p>对象：<b>{{ vioDlg.user?.name }}</b>（{{ vioDlg.user?.student_id }}）</p>
      <el-form label-width="96px">
        <el-form-item label="所属活动" required>
          <el-select v-model="vio.activity_id" filterable placeholder="选择活动"
                     style="width:100%" @change="refreshSuggest">
            <el-option v-for="a in finishedActs" :key="a.id" :value="a.id"
                       :label="`${a.type}类 · ${a.title}`" />
          </el-select>
        </el-form-item>
        <el-form-item label="违规类型" required>
          <el-radio-group v-model="vio.kind" @change="refreshSuggest">
            <el-radio-button v-for="(label, code) in kinds" :key="code" :value="code">
              {{ label }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="vio.kind === 'leave'" label="请假证明">
          <el-checkbox v-model="vio.has_proof" @change="refreshSuggest">
            有证明（病假／事假，参照学校请假标准）
          </el-checkbox>
        </el-form-item>
        <el-form-item label="情况简述" required>
          <el-input v-model="vio.detail" type="textarea" :rows="2"
                    placeholder="记录具体情况；违规类型为「其他」时请尽量详细" />
        </el-form-item>
      </el-form>

      <template v-if="vio.suggest">
        <div class="suggest">
          <div class="suggest-head">
            规范建议 · 该志愿者同类违规第 <b>{{ vio.suggest.history_count }}</b> 次
          </div>
          <p class="basis">{{ vio.suggest.basis }}</p>
          <p v-for="(n, i) in vio.suggest.notes" :key="i" class="note">需人工判断：{{ n }}</p>
          <el-form label-width="96px" style="margin-top:10px">
            <el-form-item label="限制类型">
              <el-checkbox-group v-model="vio.types">
                <el-checkbox v-for="(t, k) in types" :key="k" :value="k">
                  {{ k }} {{ t.name }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="限制至">
              <el-date-picker v-model="vio.until" type="date" style="width:100%" />
            </el-form-item>
          </el-form>
          <div class="dim">
            默认按规范填好，可按实际情形增减后再提交；生效口径以此处为准，全程留痕。
          </div>
        </div>
      </template>

      <template #footer>
        <el-button @click="vioDlg.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!vio.suggest" @click="saveViolation">
          登记并生效
        </el-button>
      </template>
    </el-dialog>

    <!-- 限制准入 -->
    <el-dialog v-model="restrictDlg.visible" title="设置限制准入（《志愿者服务规范》）" width="460">
      <p>对象：<b>{{ restrictDlg.user?.name }}</b></p>
      <el-checkbox-group v-model="restrictForm.types">
        <el-checkbox value="A">A 常规单次</el-checkbox>
        <el-checkbox value="B">B 常规长期</el-checkbox>
        <el-checkbox value="C">C 大型校级</el-checkbox>
        <el-checkbox value="D">D 重大活动</el-checkbox>
      </el-checkbox-group>
      <el-date-picker v-model="restrictForm.until" type="date" placeholder="截止日期"
                      style="margin-top:10px;width:100%" />
      <div class="dim" style="margin-top:6px">
        判定规则：活动**发生时间**在限制期内才拦截；招募在期内、活动已出期的允许报名。
      </div>
      <template #footer>
        <el-button @click="restrictDlg.visible = false">取消</el-button>
        <el-button type="primary" @click="saveRestrict">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const users = ref([])
const colleges = ref([])
const q = ref('')
const college = ref('')
const loading = ref(false)
const creditDlg = ref({ visible: false, user: null })
const creditForm = ref({ delta: -5, reason: '' })
const restrictDlg = ref({ visible: false, user: null })
const restrictForm = ref({ types: [], until: '' })
const vioDlg = ref({ visible: false, user: null })
const vio = ref({ activity_id: '', kind: 'late', has_proof: false, detail: '',
                  types: [], until: '', suggest: null })
const kinds = ref({})
const types = ref({})
const finishedActs = ref([])

async function load() {
  loading.value = true
  try {
    users.value = await api.get(`/api/staff/users?q=${encodeURIComponent(q.value)}` +
      (college.value ? `&college=${encodeURIComponent(college.value)}` : ''))
  } finally { loading.value = false }
}
onMounted(async () => {
  const c = await api.get('/api/auth/constants')
  colleges.value = c.colleges
  kinds.value = c.violation_kinds
  types.value = c.activity_types
  load()
})

// —— 违规登记 ——
function openViolation(row) {
  vioDlg.value = { visible: true, user: row }
  vio.value = { activity_id: '', kind: 'late', has_proof: false, detail: '',
                types: [], until: '', suggest: null }
  loadActivities()
}

async function loadActivities() {
  // 违规在活动结束后登记，但已截止的活动也可能需要（如报名后失联）
  const all = await api.get('/api/staff/activities')
  finishedActs.value = all.filter(a => a.status !== 'draft')
}

// 活动/类型/证明任一变化都重算建议：累计次数与活动类型都会改变结论
async function refreshSuggest() {
  if (!vio.value.activity_id || !vio.value.kind) return
  const d = await api.post('/api/staff/violations/preview', {
    user_id: vioDlg.value.user.id, activity_id: vio.value.activity_id,
    kind: vio.value.kind, has_proof: vio.value.has_proof })
  vio.value.suggest = d
  vio.value.types = [...d.suggest_types]
  vio.value.until = d.suggest_until
}

async function saveViolation() {
  const v = vio.value
  if (!v.detail.trim()) return ElMessage.warning('请填写违规情况简述')
  await api.post('/api/staff/violations', {
    user_id: vioDlg.value.user.id, activity_id: v.activity_id, kind: v.kind,
    has_proof: v.has_proof, detail: v.detail, types: v.types,
    until: v.until ? new Date(v.until).toISOString().slice(0, 10) : null,
  })
  ElMessage.success('已登记违规并更新限制准入')
  vioDlg.value.visible = false
  load()
}

function openCredit(row) {
  creditDlg.value = { visible: true, user: row }
  creditForm.value = { delta: -5, reason: '' }
}
async function saveCredit() {
  if (!creditForm.value.reason.trim()) return ElMessage.warning('请填写事由')
  const d = await api.post(`/api/staff/users/${creditDlg.value.user.id}/credit`, creditForm.value)
  ElMessage.success(`已调整为 ${d.score} 分`)
  creditDlg.value.visible = false
  load()
}
function openRestrict(row) {
  restrictDlg.value = { visible: true, user: row }
  restrictForm.value = { types: row.restricted_types || [], until: '' }
}
async function saveRestrict() {
  const u = restrictForm.value.until
  await api.post(`/api/staff/users/${restrictDlg.value.user.id}/restriction`, {
    types: restrictForm.value.types,
    until: u ? new Date(u).toISOString().slice(0, 10) : null,
  })
  ElMessage.success('已保存')
  restrictDlg.value.visible = false
  load()
}
</script>

<style scoped>
.ops { display: flex; gap: 10px; align-items: center; white-space: nowrap; }
.ops :deep(.el-button + .el-button) { margin-left: 0; }
.toolbar { display:flex; gap:12px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
.toolbar .dim { flex: 1 1 100%; }   /* 说明文字独占一行，别把工具条撑宽 */
.suggest { border:1px solid var(--el-border-color); border-radius:8px; padding:12px 14px; }
.suggest-head { font-weight:600; margin-bottom:6px; }
.basis { margin:0 0 4px; color:var(--el-color-primary); font-size:13px; }
.note { margin:2px 0; font-size:12.5px; color:#e6a23c; line-height:1.7; }
</style>

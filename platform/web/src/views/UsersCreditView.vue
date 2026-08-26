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

      <el-table :data="users" size="small" v-loading="loading" max-height="620">
        <el-table-column prop="name" label="姓名" width="90" />
        <el-table-column prop="student_id" label="学号" width="110" />
        <el-table-column prop="gender" label="性别" width="60" />
        <el-table-column prop="college" label="学院" min-width="120" />
        <el-table-column prop="phone" label="手机号" width="120" />
        <el-table-column prop="id_card_masked" label="身份证（脱敏）" width="150">
          <template #default="{row}"><span class="mono dim">{{ row.id_card_masked || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="信用分" width="90">
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
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{row}">
            <el-button size="small" link type="primary"
                       @click="openCredit(row)">调整信用</el-button>
            <el-button size="small" link @click="openRestrict(row)">限制准入</el-button>
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

const users = ref([])
const colleges = ref([])
const q = ref('')
const college = ref('')
const loading = ref(false)
const creditDlg = ref({ visible: false, user: null })
const creditForm = ref({ delta: -5, reason: '' })
const restrictDlg = ref({ visible: false, user: null })
const restrictForm = ref({ types: [], until: '' })

async function load() {
  loading.value = true
  try {
    users.value = await api.get(`/api/staff/users?q=${encodeURIComponent(q.value)}` +
      (college.value ? `&college=${encodeURIComponent(college.value)}` : ''))
  } finally { loading.value = false }
}
onMounted(async () => {
  colleges.value = (await api.get('/api/auth/constants')).colleges
  load()
})

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
.toolbar { display:flex; gap:12px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
</style>

<!-- 角色管理。单开一页且只给管理员：这一页改的是权限本身。 -->
<template>
  <div class="page">
    <h2>角色管理</h2>
    <el-alert type="warning" :closable="false" style="margin-bottom:12px">
      这一页改的是<b>权限</b>：谁能进工作台、谁能审批、谁是部长。
      用户类型（老师/学生）由数智人大下发，平台不改；<b>不能移除自己的管理员身份</b>，
      如需交接请由另一位管理员操作。
    </el-alert>

    <div class="toolbar">
      <el-input v-model="q" placeholder="按姓名或学号搜索（给学生加身份时用）"
                style="width:280px" clearable @keyup.enter="load" @clear="load" />
      <el-button type="primary" @click="load">查询</el-button>
      <RefreshBar ref="refresher" :load="load" />
      <span class="dim">不搜索时只列已有管理类身份的人</span>
    </div>

    <DataCards v-if="narrow" :rows="rows" title-key="name" v-loading="loading"
               row-key="id"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'college', label: '单位' },
                         { key: 'role_label', label: '身份' },
                         { key: 'post_label', label: '青协职务', hideEmpty: true },
                         { key: 'department', label: '部门', hideEmpty: true }]"
               empty="没有匹配的人">
      <template #extra="{ row }">
        <el-tag v-if="row.is_me" size="small" type="warning" effect="plain">这是你</el-tag>
      </template>
      <template #actions="{ row }">
        <el-button size="small" type="primary" plain @click="open(row)">调整身份</el-button>
      </template>
    </DataCards>

    <el-table v-else :data="rows" size="small" v-loading="loading" max-height="620">
      <el-table-column prop="name" label="姓名" width="100">
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.is_me" size="small" type="warning" effect="plain">你</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="student_id" label="学号" width="120" />
      <el-table-column prop="college" label="单位" min-width="140" />
      <el-table-column label="类型" width="70">
        <template #default="{ row }">{{ row.user_type === 'teacher' ? '老师' : '学生' }}</template>
      </el-table-column>
      <el-table-column label="身份" min-width="180">
        <template #default="{ row }">
          <el-tag v-for="r in row.roles" :key="r" size="small" effect="plain"
                  :type="ROLE_TAG[r]" style="margin-right:4px">
            {{ ROLE_LABELS[r] || r }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="青协职务" min-width="150">
        <template #default="{ row }">
          <template v-if="row.roles.includes('qingxie')">
            {{ row.post_label }}
            <span v-if="row.department" class="dim">· {{ row.department }}</span>
          </template>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="open(row)">调整身份</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg.visible" title="调整身份" width="480">
      <p style="margin-top:0">
        <b>{{ dlg.row?.name }}</b>
        <span class="dim">（{{ dlg.row?.student_id }}·{{ dlg.row?.college }}）</span>
      </p>
      <el-form label-width="88px">
        <el-form-item label="身份" required>
          <el-checkbox-group v-model="dlg.roles">
            <el-checkbox v-for="r in ROLE_ORDER" :key="r" :value="r"
                         :disabled="r === 'admin' && dlg.row?.is_me">
              {{ ROLE_LABELS[r] }}
            </el-checkbox>
          </el-checkbox-group>
          <div v-if="dlg.row?.is_me" class="dim">不能移除自己的管理员身份</div>
        </el-form-item>
        <template v-if="dlg.roles.includes('qingxie')">
          <el-form-item label="青协职务">
            <el-radio-group v-model="dlg.post">
              <el-radio value="member">干事</el-radio>
              <el-radio value="head">部长</el-radio>
              <el-radio value="president">会长</el-radio>
            </el-radio-group>
            <div class="dim">部长是本部门活动的默认审批人；会长可接管任何一条审批</div>
          </el-form-item>
          <el-form-item :label="dlg.post === 'head' ? '所属部门' : '所属部门'"
                        :required="dlg.post === 'head'">
            <el-select v-model="dlg.deptId" clearable placeholder="选择部门"
                       style="width:100%">
              <el-option v-for="d in depts" :key="d.id" :value="d.id" :label="d.name" />
            </el-select>
            <div v-if="dlg.post === 'head'" class="dim">
              设为部长必须选部门，否则审批找不到人
            </div>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="dlg.busy" @click="save">保存</el-button>
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
import RefreshBar from '../components/RefreshBar.vue'

const ROLE_ORDER = ['admin', 'qingxie', 'initiator', 'student']
const ROLE_LABELS = { admin: '管理员', qingxie: '青协', initiator: '活动发起方', student: '学生' }
const ROLE_TAG = { admin: 'danger', qingxie: 'success', initiator: 'primary', student: 'info' }

const narrow = useNarrow()
const rows = ref([])
const depts = ref([])
const q = ref('')
const loading = ref(false)
const refresher = ref(null)
const dlg = ref({ visible: false, row: null, roles: [], post: 'member', deptId: '', busy: false })

async function load() {
  loading.value = true
  try {
    rows.value = await api.get(`/api/staff/roles?q=${encodeURIComponent(q.value.trim())}`)
  } finally { loading.value = false }
}

onMounted(async () => {
  depts.value = await api.get('/api/staff/departments')
  await load()
  refresher.value?.markLoaded()
})

function open(row) {
  dlg.value = {
    visible: true, row,
    roles: [...row.roles],
    post: row.qingxie_post || 'member',
    deptId: row.department_id || '',
    busy: false,
  }
}

async function save() {
  const d = dlg.value
  if (!d.roles.length) return ElMessage.warning('至少要保留一个身份')
  d.busy = true
  try {
    await api.put(`/api/staff/roles/${d.row.id}`,
                  { roles: d.roles, qingxie_post: d.post, department_id: d.deptId })
    ElMessage.success('已保存')
    d.visible = false
    load()
  } finally { d.busy = false }
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
</style>

<!-- 角色管理。管理员与会长团管全部；部长只管本部门的骨干与成员；骨干只认领本部门成员。 -->
<template>
  <div class="page">
    <h2>角色管理</h2>
    <el-alert type="warning" :closable="false">
      <template v-if="coreOnly">
        你是<b>{{ myDeptName }}</b>骨干：此处列出本部门成员，点「归我管理」将成员归到自己名下，
        其可见范围与你一致。其他身份与层级请联系部长。
      </template>
      <template v-else-if="headOnly">
        你是<b>{{ myDeptName }}</b>部长：可设置或撤销本部门的骨干与成员。
        仅能调整青协身份，管理员、活动发起方、学生身份请联系会长团。
      </template>
      <template v-else>
        本页调整的是<b>权限</b>：谁能进入工作台、谁能审批、谁是部长。
        用户类型（老师/学生）由统一身份认证下发，平台不作修改；<b>不能移除自己的管理员身份</b>，
        如需交接请由另一位管理员操作。
      </template>
    </el-alert>

    <div class="toolbar">
      <el-input v-model="q" placeholder="按姓名或学号搜索"
                style="width:280px" clearable @keyup.enter="load" @clear="load" />
      <el-button type="primary" @click="load">查询</el-button>
      <RefreshBar :load="load" :at="loadedAt" />
      <span class="dim">{{ coreOnly ? '只列本部门的成员' : headOnly ? '不搜索时只列本部门的人，添加新成员请搜索学号' : '不搜索时只列已有管理类身份的人' }}</span>
    </div>

    <DataCards v-if="narrow" :rows="rows" title-key="name" v-loading="loading"
               row-key="id"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'college', label: '单位' },
                         { key: 'role_label', label: '身份' },
                         { key: 'post_label', label: '青协层级', hideEmpty: true },
                         { key: 'department', label: '部门', hideEmpty: true },
                         { key: 'supervisor', label: '归属骨干', hideEmpty: true }]"
               empty="没有匹配的人">
      <template #extra="{ row }">
        <el-tag v-if="row.is_me" size="small" type="warning" effect="plain">这是你</el-tag>
        <el-tag v-if="coreOnly && row.supervisor_id === auth.user.id" size="small"
                type="success" effect="plain">归我管理</el-tag>
      </template>
      <template #actions="{ row }">
        <el-button v-if="coreOnly" size="small" type="primary" plain
                   :disabled="row.supervisor_id === auth.user.id"
                   @click="claim(row)">归我管理</el-button>
        <el-button v-else size="small" type="primary" plain @click="open(row)">调整身份</el-button>
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
      <el-table-column label="青协层级" min-width="150">
        <template #default="{ row }">
          <template v-if="row.roles.includes('qingxie')">
            {{ row.post_label }}
            <span v-if="row.department" class="dim">· {{ row.department }}</span>
            <span v-if="row.supervisor" class="dim">· 归属 {{ row.supervisor }}</span>
          </template>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button v-if="coreOnly" size="small" link type="primary"
                     :disabled="row.supervisor_id === auth.user.id" @click="claim(row)">
            {{ row.supervisor_id === auth.user.id ? '已归我管理' : '归我管理' }}
          </el-button>
          <el-button v-else size="small" link type="primary" @click="open(row)">调整身份</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg.visible" title="调整身份" width="480">
      <p>
        <b>{{ dlg.row?.name }}</b>
        <span class="dim">（{{ dlg.row?.student_id }}·{{ dlg.row?.college }}）</span>
      </p>
      <el-form label-width="88px">
        <el-form-item label="身份" required>
          <el-checkbox-group v-model="dlg.roles">
            <!-- 部长只能动青协身份：别的身份（含学生）显示但锁住 -->
            <el-checkbox v-for="r in ROLE_ORDER" :key="r" :value="r"
                         :disabled="(r === 'admin' && dlg.row?.is_me)
                                    || (headOnly && r !== 'qingxie')">
              {{ ROLE_LABELS[r] }}
            </el-checkbox>
          </el-checkbox-group>
          <div v-if="dlg.row?.is_me && !headOnly" class="dim">不能移除自己的管理员身份</div>
        </el-form-item>
        <template v-if="dlg.roles.includes('qingxie')">
          <el-form-item label="青协层级">
            <el-radio-group v-model="dlg.post">
              <el-radio value="member">部门成员</el-radio>
              <el-radio value="core">部门骨干</el-radio>
              <el-radio value="head" :disabled="headOnly">部长</el-radio>
              <el-radio value="council" :disabled="headOnly">会长团</el-radio>
            </el-radio-group>
            <div class="dim">
              会长团：除集成设置外与管理员同权 · 部长：审批本部门活动，管理本部门骨干与成员 ·
              骨干：只管理自己参与的活动 · 成员：可见范围与归属骨干一致，可查看但不能保存
            </div>
          </el-form-item>
          <el-form-item v-if="dlg.post !== 'council'" label="所属部门" required>
            <el-select v-model="dlg.deptId" placeholder="选择部门" style="width:100%"
                       :disabled="headOnly">
              <el-option v-for="d in depts" :key="d.id" :value="d.id" :label="d.name" />
            </el-select>
            <div class="dim">骨干与成员须归属部门，部长才能管理</div>
          </el-form-item>
          <!-- 成员是跟着某个人学做事的，可见范围就是那位骨干的可见范围 -->
          <el-form-item v-if="dlg.post === 'member'" label="归属骨干" required>
            <el-select v-model="dlg.supervisorId" placeholder="选择本部门的骨干"
                       style="width:100%" :disabled="!dlg.deptId">
              <el-option v-for="c in cores" :key="c.id" :value="c.id"
                         :label="`${c.name}（${c.student_id}）`" />
            </el-select>
            <div class="dim">
              成员的可见范围与归属骨干一致。
              <template v-if="dlg.deptId && !cores.length">该部门还没有骨干，请先设置一位。</template>
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
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'
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
const loadedAt = ref(0)
const dlg = ref({ visible: false, row: null, roles: [], post: 'member', deptId: '', busy: false })
// 部长视角：只能在本部门里设骨干/成员；骨干视角：只认领本部门成员
const headOnly = computed(() =>
  auth.activeIdentity === 'qingxie' && auth.user?.qingxie_post === 'head')
const coreOnly = computed(() =>
  auth.activeIdentity === 'qingxie' && auth.user?.qingxie_post === 'core')

// 骨干认领成员：身份、层级、部门原样送回，只改归属
async function claim(row) {
  await api.put(`/api/staff/roles/${row.id}`,
                { roles: row.roles, qingxie_post: row.qingxie_post,
                  department_id: row.department_id, supervisor_id: auth.user.id })
  ElMessage.success(`${row.name} 已归你管理`)
  load()
}
const myDeptName = computed(() =>
  depts.value.find(d => d.id === auth.user?.department_id)?.name || '')

async function load() {
  loading.value = true
  try {
    rows.value = await api.get(`/api/staff/roles?q=${encodeURIComponent(q.value.trim())}`)
    loadedAt.value = Date.now()
  } finally { loading.value = false }
}

onMounted(async () => {
  depts.value = await api.get('/api/staff/departments')
  await load()
})

function open(row) {
  dlg.value = {
    visible: true, row,
    roles: [...row.roles],
    post: row.qingxie_post || 'member',
    // 部长拉人进部门：部门就是他自己的，不用选
    deptId: row.department_id || (headOnly.value ? auth.user.department_id : ''),
    supervisorId: row.supervisor_id || '',
    busy: false,
  }
  loadCores()
}

// 归属骨干的候选：跟着所选部门走
const cores = ref([])
async function loadCores() {
  const dept = dlg.value.deptId
  cores.value = dept
    ? await api.get(`/api/staff/roles/cores?department_id=${dept}`)
    : []
}
watch(() => dlg.value.deptId, loadCores)

async function save() {
  const d = dlg.value
  if (!d.roles.length) return ElMessage.warning('至少要保留一个身份')
  d.busy = true
  try {
    await api.put(`/api/staff/roles/${d.row.id}`,
                  { roles: d.roles, qingxie_post: d.post, department_id: d.deptId,
                    supervisor_id: d.supervisorId })
    ElMessage.success('已保存')
    d.visible = false
    load()
  } finally { d.busy = false }
}
</script>

<style scoped>
</style>
